"""District lookup with exact PINs before ambiguous postal prefixes."""
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@lru_cache(maxsize=1)
def mapping():
    master = json.loads((ROOT / "data/mp_districts.json").read_text())
    districts = master.get("districts", []) if isinstance(master, dict) else master
    try:
        exact = json.loads((ROOT / "data/pincode_districts.json").read_text()).get("districts_by_pin", {})
    except (OSError, ValueError):
        exact = {}
    return districts, exact

def mentions(text, value):
    return bool(value and re.search(r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)", text))

def resolve_district(row):
    districts, exact = mapping()
    pin = str(row.get("Pincode") or "").strip()
    texts = [str(row.get(field) or "").casefold() for field in
             ("Location", "Title", "Work Description", "Organisation", "Department", "Division", "Sub Division")]
    by_name = {district["name"]: district for district in districts}
    candidates = exact.get(pin, [])
    if not candidates:
        matches = [(str(prefix), d["name"]) for d in districts for prefix in d.get("pinPrefixes", [])
                   if pin and pin.startswith(str(prefix))]
        longest = max((len(prefix) for prefix, _ in matches), default=0)
        candidates = sorted({name for prefix, name in matches if len(prefix) == longest})
    if len(candidates) == 1:
        return candidates[0]
    search_names = candidates or list(by_name)
    for text in texts:
        mentioned = [name for name in search_names if any(mentions(text, alias) for alias in
                     [name] + by_name.get(name, {}).get("aliases", []))]
        if len(mentioned) == 1:
            return mentioned[0]
    saved = str(row.get("District") or "").strip()
    return saved if saved and saved in candidates else ""
