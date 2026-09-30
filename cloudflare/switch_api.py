"""Switch the existing frontend only after the Worker and migrated data pass QA."""
import argparse
from pathlib import Path
from urllib.parse import urlsplit

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("worker_url")
    args = parser.parse_args()
    parsed = urlsplit(args.worker_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".workers.dev") or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.port:
        raise ValueError("Use the verified HTTPS workers.dev deployment origin")
    target = args.worker_url.rstrip("/")
    root = Path(__file__).resolve().parents[1]
    old = "https://mp-tenders-api.onrender.com"
    for name in ("index.html", "admin/index.html"):
        path = root / name
        source = path.read_text()
        if old not in source:
            raise ValueError(f"Existing API origin not found in {name}; review before switching")
        path.write_text(source.replace(old, target))
    print("Frontend API origin switched. Review and publish the two changed files.")

if __name__ == "__main__":
    main()
