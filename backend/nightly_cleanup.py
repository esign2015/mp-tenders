"""Purge expired tender records at the daily 7 PM IST run; no archive."""
import csv
import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
DATA_FILES = ('all_tenders_org_detailed.csv', 'tender_details.csv', 'organisation_tenders.csv')


def parse_dt(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    for fmt in ('%d/%b/%Y %I:%M %p', '%d-%b-%Y %I:%M %p', '%d/%m/%Y %I:%M %p',
                '%d-%m-%Y %I:%M %p', '%d/%b/%Y %H:%M', '%d-%b-%Y %H:%M',
                '%d/%m/%Y %H:%M', '%d-%m-%Y %H:%M'):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    except ValueError:
        return None


def purge_csv(data, cutoff):
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    fields = list(reader.fieldnames or [])
    if 'Tender ID' not in fields or 'Closing Date' not in fields:
        return data, []
    rows = list(reader)
    kept, removed = [], []
    for row in rows:
        closing = parse_dt(row.get('Closing Date'))
        if closing and closing <= cutoff:
            removed.append(row.get('Tender ID', '').strip())
        else:
            kept.append(row)  # Missing/unparseable deadlines must not be guessed.
    if not removed:
        return data, []
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(kept)  # A valid header-only file is allowed when all expired.
    return stream.getvalue().encode('utf-8-sig'), removed


def cleanup(root=ROOT, now=None):
    now = now or datetime.now(IST)
    report = {'status': 'completed', 'policy': 'expired-at-evening', 'cleaned_at': now.isoformat(),
              'retention_hours': 0, 'files': {}}
    removed_ids = set()
    for name in DATA_FILES:
        path = root / name
        if not path.exists():
            continue
        data, removed = purge_csv(path.read_bytes(), now)
        if removed:
            temp = path.with_suffix('.tmp')
            temp.write_bytes(data)
            temp.replace(path)
        report['files'][name] = {'removed': len(removed)}
        removed_ids.update(removed)
    snapshot = root / 'data/live_snapshot.json'
    if snapshot.exists():
        value = json.loads(snapshot.read_text())
        # An old detail CSV can have a pre-extension deadline. Its removal
        # must not remove a still-open ID from the current portal inventory.
        listing=root/'organisation_tenders.csv'
        if listing.exists():
            with listing.open(encoding='utf-8-sig',newline='') as stream:
                retained={row['Tender ID'] for row in csv.DictReader(stream)}
            value['tender_ids']=[tid for tid in value.get('tender_ids',[]) if tid in retained]
        else:
            value['tender_ids'] = [tid for tid in value.get('tender_ids', []) if tid not in removed_ids]
        snapshot.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    report['removed'] = len(removed_ids)
    (root / 'data').mkdir(exist_ok=True)
    (root / 'data/cleanup_status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    print(json.dumps(cleanup(), ensure_ascii=False, indent=2))
