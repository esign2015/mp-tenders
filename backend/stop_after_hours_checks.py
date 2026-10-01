"""Cancel only after-hours runs whose unfinished jobs are standalone checks."""
import json
import os
from urllib.request import Request, urlopen
from checks_cutoff import seconds_left

TARGETS = {"latest", "corrigendum_watch", "corrigendum_urgent"}

def safe_to_cancel(jobs):
    unfinished = {j["name"] for j in jobs if j["status"] != "completed"}
    return bool(unfinished) and unfinished <= TARGETS

def main():
    if seconds_left() > 0:
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    current = int(os.environ["GITHUB_RUN_ID"])
    token = os.environ["GH_TOKEN"]
    def api(path, method="GET"):
        req = Request("https://api.github.com/repos/" + repo + path,
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/vnd.github+json"},
                      method=method)
        with urlopen(req, timeout=30) as response:
            data = response.read()
            return json.loads(data) if data else {}
    for state in ("in_progress", "queued"):
        runs = api("/actions/runs?status=" + state + "&per_page=100")["workflow_runs"]
        for run in runs:
            if run["id"] == current or run["path"] not in (
                ".github/workflows/scrape.yml", ".github/workflows/home-latest.yml"):
                continue
            jobs = []
            page = 1
            while True:
                batch = api(f"/actions/runs/{run['id']}/jobs?per_page=100&page={page}")["jobs"]
                jobs.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            if safe_to_cancel(jobs):
                api(f"/actions/runs/{run['id']}/cancel", "POST")
                print(f"Cancelled standalone after-hours check run {run['id']}", flush=True)
            else:
                print(f"Preserved run {run['id']}: other jobs still unfinished", flush=True)

if __name__ == "__main__":
    main()
