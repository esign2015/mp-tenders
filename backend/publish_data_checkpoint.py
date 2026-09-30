"""Publish data on the latest remote tree without rebasing an active scraper.

Only locally changed files are overlaid. Detail CSVs preserve remote IDs and
nonempty fields, so independent list/detail jobs cannot drop each other's work.
"""
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from inventory_summary import detail_complete, write_summary
from nightly_cleanup import DATA_FILES, parse_dt, purge_csv

DETAIL_FILES = {"all_tenders_org_detailed.csv", "tender_details.csv"}

def git(*args, data=None, env=None):
    result = subprocess.run(["git", *args], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout

def blob(ref, path):
    try:
        return git("show", f"{ref}:{path}")
    except RuntimeError:
        return b""

def merge_details(remote, local):
    def parse(data):
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        return list(reader.fieldnames or []), list(reader)
    remote_fields, remote_rows = parse(remote)
    local_fields, local_rows = parse(local)
    fields = list(dict.fromkeys(remote_fields + local_fields))
    rows = {row.get("Tender ID", "").strip(): row for row in remote_rows if row.get("Tender ID", "").strip()}
    for row in local_rows:
        tid = row.get("Tender ID", "").strip()
        if not tid:
            continue
        old = rows.get(tid, {})
        # Preserve a newer completed detail record from another worker.
        remote_newer = (detail_complete(old) and
                        (not detail_complete(row) or str(old.get("Tested At", "")) > str(row.get("Tested At", ""))))
        merged = dict(row) if remote_newer else dict(old)
        preferred = old if remote_newer else row
        merged.update({key: value for key, value in preferred.items() if str(value or "").strip()})
        rows[tid] = merged
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows.values())
    return stream.getvalue().encode("utf-8-sig")

def publish(paths):
    branch = os.getenv("GITHUB_REF_NAME", "main")
    cache_file = Path(os.getenv("RUNNER_TEMP", "/tmp")) / "mp-published-digests.json"
    try:
        previous = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        previous = {}
    changed = {}
    digests = {}
    for path in paths:
        source = Path(path)
        if not source.is_file():
            continue
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        baseline = previous.get(path, hashlib.sha256(blob("HEAD", path)).hexdigest())
        if digest != baseline:
            changed[path] = data
            digests[path] = digest
    if not changed:
        print("No new data checkpoint.")
        return
    for attempt in range(3):
        git("fetch", "origin", branch)
        parent = git("rev-parse", f"origin/{branch}").decode().strip()
        updates = dict(changed)
        for path in DETAIL_FILES & updates.keys():
            updates[path] = merge_details(blob(parent, path), updates[path])
        # The cleanup cutoff survives concurrent writers, so an older worker
        # cannot restore a purged row. A future extended deadline remains valid.
        cleanup_mode = os.getenv("EXPIRED_CLEANUP") == "1"
        cleanup_bytes = updates.get("data/cleanup_status.json", blob(parent, "data/cleanup_status.json"))
        try:
            cleanup_report = json.loads(cleanup_bytes)
            cutoff = parse_dt(cleanup_report.get("cleaned_at")) if cleanup_report.get("policy") == "expired-at-evening" else None
        except (ValueError, TypeError):
            cutoff = None
        if cutoff:
            removed_ids = set()
            for path in DATA_FILES:
                if path not in updates and not cleanup_mode:
                    continue
                # Cleanup uses the latest remote bytes, not its earlier checkout.
                data = blob(parent, path) if cleanup_mode else updates[path]
                if not data:
                    continue
                purged, removed = purge_csv(data, cutoff)
                updates[path] = purged
                removed_ids.update(removed)
                if cleanup_mode:
                    cleanup_report.setdefault("files", {})[path] = {"removed": len(removed)}
            if cleanup_mode:
                cleanup_report["removed"] = len(removed_ids)
                updates["data/cleanup_status.json"] = json.dumps(cleanup_report, ensure_ascii=False, indent=2).encode()
            snapshot_bytes = blob(parent, "data/live_snapshot.json") if cleanup_mode else updates.get("data/live_snapshot.json")
            if snapshot_bytes:
                snapshot = json.loads(snapshot_bytes)
                listing_bytes=updates.get("organisation_tenders.csv",blob(parent,"organisation_tenders.csv"))
                if listing_bytes:
                    retained={r.get("Tender ID","").strip() for r in csv.DictReader(io.StringIO(listing_bytes.decode("utf-8-sig")))}
                    snapshot["tender_ids"]=[tid for tid in snapshot.get("tender_ids",[]) if tid in retained]
                else:
                    snapshot["tender_ids"] = [tid for tid in snapshot.get("tender_ids", []) if tid not in removed_ids]
                updates["data/live_snapshot.json"] = json.dumps(snapshot, ensure_ascii=False, indent=2).encode()
        # Derive progress from the exact CSV bytes being published together.
        with tempfile.TemporaryDirectory() as summary_dir:
            root = Path(summary_dir)
            for path in ("organisations.csv", "organisation_tenders.csv", "all_tenders_org_detailed.csv", "data/existing_id_detail_status.json"):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(updates.get(path, blob(parent, path)))
            write_summary(root)
            updates["data/inventory_summary.json"] = (root / "data/inventory_summary.json").read_bytes()
        with tempfile.TemporaryDirectory() as index_dir:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(index_dir) / "index"))
            git("read-tree", parent, env=env)
            for path, data in updates.items():
                sha = git("hash-object", "-w", "--stdin", data=data).decode().strip()
                git("update-index", "--add", "--cacheinfo", f"100644,{sha},{path}", env=env)
            tree = git("write-tree", env=env).decode().strip()
            commit = git("commit-tree", tree, "-p", parent,
                         data=b"Publish tender data checkpoint [skip ci]\n").decode().strip()
        try:
            git("push", "origin", f"{commit}:refs/heads/{branch}")
        except RuntimeError:
            if attempt == 2:
                raise
            continue
        previous.update(digests)
        cache_file.write_text(json.dumps(previous))
        print(f"Published {len(updates)} data files: {commit}")
        return

if __name__ == "__main__":
    publish(sys.argv[1:])
