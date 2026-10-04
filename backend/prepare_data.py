"""Hydrate current runtime state without checking out old application code."""
import hashlib,json,os,subprocess
from pathlib import Path
from data_branch import DATA_BRANCH,runtime_path

def prepare():
    if os.getenv("GITHUB_ACTIONS")=="true":
        subprocess.run(["git","config","user.name","mp-tenders-bot"],check=True)
        subprocess.run(["git","config","user.email","actions@github.com"],check=True)
    subprocess.run(["git","fetch","--depth=1","origin",DATA_BRANCH],check=True)
    ref="origin/"+DATA_BRANCH
    paths=subprocess.check_output(["git","ls-tree","-r","--name-only",ref],text=True).splitlines()
    baseline={}
    for path in paths:
        if not runtime_path(path):continue
        data=subprocess.check_output(["git","show",ref+":"+path]);target=Path(path)
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        baseline[path]=hashlib.sha256(data).hexdigest()
    cache=Path(os.getenv("RUNNER_TEMP","/tmp"))/"mp-published-digests.json"
    cache.write_text(json.dumps(baseline))
    from runtime_cutover import sync
    if os.getenv("TENDER_RUNTIME_READ_ONLY")!="1":sync()
    print("Loaded runtime state from",DATA_BRANCH,"without replacing application code:",len(baseline),"files")
if __name__=="__main__":prepare()
