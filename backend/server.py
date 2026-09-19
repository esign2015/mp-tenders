import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from scraper import scrape_mp_tenders

app = Flask(__name__)
CORS(app)

ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "all_tenders_org_detailed.csv"
FIELDS = [
    "Tender ID", "Published Date", "Closing Date", "Opening Date",
    "Title", "Reference Number", "Organisation", "Department",
    "Division", "Sub Division", "PAC Amount", "EMD Fee",
    "Tender Fee", "Processing Fee", "Total Fee", "Status", "URL",
]


def read_rows():
    if not CSV_FILE.exists():
        return []
    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@app.get("/")
def home():
    return jsonify({
        "status": "online",
        "message": "MP Tenders scraper API is running",
        "source": "https://www.mptenders.gov.in/nicgep/app",
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "csv_exists": CSV_FILE.exists(),
        "records": len(read_rows()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/tenders")
def tenders():
    return jsonify({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(read_rows()),
        "tenders": read_rows(),
    })


@app.post("/api/fetch")
def fetch():
    return jsonify(scrape_mp_tenders(CSV_FILE))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
