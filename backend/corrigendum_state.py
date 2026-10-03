"""Preserve the newest official presence/absence check across CSV writers."""
from datetime import datetime, timedelta, timezone

FIELDS = ('Corrigendum', 'Corrigendum Type', 'Corrigendum Detected At', 'Corrigendum Last Checked')


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value or ''))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError):
        return None


def merge_state(result, *sources):
    checked = [(stamp(row.get('Corrigendum Last Checked')), row) for row in sources
               if 'Corrigendum' in row and stamp(row.get('Corrigendum Last Checked'))]
    if not checked:
        return result
    _, winner = max(enumerate(checked), key=lambda item: (item[1][0], item[0]))[1]
    for key in FIELDS:
        result[key] = winner.get(key, '')
    if str(result['Corrigendum'] or '').strip().casefold() in ('', 'no', 'none', 'false', '0'):
        # Explicit absence also survives older CSV writers that keep nonempty fields.
        result['Corrigendum'] = 'No'
        result['Corrigendum Type'] = ''
        result['Corrigendum Detected At'] = ''
    elif not result['Corrigendum Detected At']:
        prior = [row.get('Corrigendum Detected At') for _, row in checked
                 if row.get('Corrigendum') == result['Corrigendum']
                 and row.get('Corrigendum Type', '') == result['Corrigendum Type']
                 and stamp(row.get('Corrigendum Detected At'))]
        result['Corrigendum Detected At'] = min(prior, key=stamp) if prior else result['Corrigendum Last Checked']
    return result


def recheck_due(row):
    value = str(row.get('Corrigendum') or '').strip().casefold()
    if not value or value in ('no', 'none', 'false', '0'):
        return False
    checked = stamp(row.get('Corrigendum Last Checked'))
    extracted = stamp(row.get('Tested At'))
    # Parsing the page precedes saving its details by a few milliseconds.
    return checked is None or bool(extracted and checked + timedelta(minutes=1) < extracted)
