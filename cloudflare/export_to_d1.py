"""Convert the authenticated private migration snapshot to D1 import SQL.

Never writes private user information into the repository or stdout.
The original Render database is retained during migration verification.
"""
import argparse
import json
import os
from pathlib import Path

USER_FIELDS = ("telegram_id", "first_name", "last_name", "username", "name", "mobile",
               "mobile_verified", "signup_at", "last_login_at", "login_count", "email", "district", "state")
EVENT_FIELDS = ("id", "telegram_id", "login_at")
NUMERIC = {"telegram_id", "id", "mobile_verified", "login_count"}

def value_sql(field, value):
    if field in NUMERIC:
        return str(int(value or 0))
    return "'" + str(value or "").replace("'", "''").replace("\x00", "") + "'"

def build_sql(snapshot):
    if snapshot.get("version") != 1:
        raise ValueError("Unsupported snapshot version")
    lines = []
    for table, fields in (("users", USER_FIELDS), ("login_events", EVENT_FIELDS)):
        records = snapshot.get(table)
        if not isinstance(records, list):
            raise ValueError("Snapshot must include both user records and login history")
        seen = set()
        for row in records:
            identity = int(row[fields[0]])
            if identity in seen or identity <= 0:
                raise ValueError("Invalid or duplicate record identity")
            seen.add(identity)
            values = ",".join(value_sql(field, row.get(field)) for field in fields)
            # Plain INSERT deliberately rejects a nonempty target rather than
            # replacing user records or silently dropping migration duplicates.
            lines.append(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({values});")
    return "\n".join(lines) + "\n"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.output.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Private migration data must be saved outside the repository")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    sql = build_sql(snapshot)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(sql)
    print(json.dumps({"users": len(snapshot["users"]), "login_events": len(snapshot["login_events"]), "private_import_ready": True}))

if __name__ == "__main__":
    main()
