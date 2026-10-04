"""Runtime tender state lives on a separate branch; application code stays on main."""
import os,re
from pathlib import PurePosixPath
DATA_BRANCH=os.getenv("TENDER_DATA_BRANCH","tender-data")
ROOT_RUNTIME={"organisations.csv","organisation_tenders.csv","all_tenders_org_detailed.csv","tender_details.csv","manual_detail_test.csv","detail_validation.csv","scrape_batch_state.json"}
STATIC_DATA={"data/index.html","data/schedule_config.json","data/mp_tehsils.json","data/mp_districts.json","data/mp_organisation_master.json","data/india_states_districts.json","data/pincode_districts.json","data/verified_detail_repairs.json"}
def runtime_path(path):
    p=PurePosixPath(path)
    return (not p.is_absolute() and ".." not in p.parts and path not in STATIC_DATA and not re.search(r"(?:^|[/_])(private|users?|accounts?|passwords?|sessions?|backups?|contacts?)(?:[_.\/]|$)",path) and
            ((path in ROOT_RUNTIME) or (p.parts[0]=="data" and p.suffix in {".json",".jsonl",".csv"})))
