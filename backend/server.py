import csv
import os
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urllib_request, parse as urllib_parse

from flask import Flask, jsonify, request, Response
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
    "Tender Fee", "Processing Fee", "Total Fee", "Location", "Pincode",
    "Status", "URL",
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


def clean(value):
    return str(value or "").strip()


def telegram_profile_photo_url(user_id):
    """
    Telegram Login's photo_url is not guaranteed to be present on every
    successful widget response. When it is missing, ask the Bot API for the
    user's latest profile photo and convert its file_id into a short-lived
    HTTPS file URL. The bot token never leaves the server.
    """
    try:
        photos = telegram_api("getUserProfilePhotos", {
            "user_id": int(user_id),
            "offset": 0,
            "limit": 1,
        })
        if not photos.get("ok"):
            return ""

        photo_sets = photos.get("result", {}).get("photos", [])
        if not photo_sets:
            return ""

        sizes = photo_sets[0]
        if not sizes:
            return ""

        # Prefer the largest available size.
        photo = max(
            sizes,
            key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0))
        )
        file_id = clean(photo.get("file_id"))
        if not file_id:
            return ""

        file_info = telegram_api("getFile", {"file_id": file_id})
        if not file_info.get("ok"):
            return ""

        file_path = clean(file_info.get("result", {}).get("file_path"))
        if not file_path:
            return ""

        token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
        return f"https://api.telegram.org/file/bot{token}/{file_path}"
    except Exception as exc:
        print(f"Telegram profile photo lookup failed: {exc}")
        return ""


def telegram_api(method, params):
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib_parse.urlencode(params).encode("utf-8")
    with urllib_request.urlopen(urllib_request.Request(url, data=data), timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


@app.get("/api/telegram/photo/<int:user_id>")
def telegram_photo(user_id):
    """Return the user's current Telegram profile photo through the server.

    Telegram file URLs are temporary, so the browser must not store the
    Bot-API file URL directly. This endpoint resolves a fresh URL each time.
    """
    try:
        photos = telegram_api("getUserProfilePhotos", {
            "user_id": int(user_id),
            "offset": 0,
            "limit": 1,
        })
        if not photos.get("ok"):
            return Response(status=404)
        photo_sets = photos.get("result", {}).get("photos", [])
        if not photo_sets or not photo_sets[0]:
            return Response(status=404)
        photo = max(photo_sets[0], key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)))
        file_id = clean(photo.get("file_id"))
        if not file_id:
            return Response(status=404)
        file_info = telegram_api("getFile", {"file_id": file_id})
        if not file_info.get("ok"):
            return Response(status=404)
        file_path = clean(file_info.get("result", {}).get("file_path"))
        if not file_path:
            return Response(status=404)
        token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        with urllib_request.urlopen(url, timeout=20) as response:
            image = response.read()
            content_type = response.headers.get("Content-Type", "image/jpeg")
        return Response(image, mimetype=content_type.split(";")[0], headers={"Cache-Control": "private, max-age=300"})
    except Exception as exc:
        print(f"Telegram profile photo proxy failed: {exc}")
        return Response(status=404)


@app.get("/api/telegram/config")
def telegram_config():
    result = telegram_api("getMe", {})
    if not result.get("ok"):
        return jsonify({"ok": False, "message": "Telegram bot configuration failed."}), 500
    return jsonify({"ok": True, "username": result["result"].get("username", "")})


@app.post("/api/telegram/verify")
def telegram_verify():
    payload = request.get_json(silent=True) or {}
    received_hash = clean(payload.get("hash"))
    if not received_hash:
        return jsonify({"verified": False, "message": "Telegram authentication data is missing."}), 400

    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    if not token:
        return jsonify({"verified": False, "message": "Telegram bot is not configured."}), 500

    auth_date = int(payload.get("auth_date", 0) or 0)
    now = int(datetime.now(timezone.utc).timestamp())
    if not auth_date or now - auth_date > 86400:
        return jsonify({"verified": False, "message": "Telegram verification expired. Please verify again."}), 401

    check_fields = {k: str(v) for k, v in payload.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={check_fields[k]}" for k in sorted(check_fields))
    secret_key = hashlib.sha256(token.encode("utf-8")).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        return jsonify({"verified": False, "message": "Telegram verification could not be validated."}), 401

    user_id = int(payload.get("id", 0) or 0)
    if not user_id:
        return jsonify({"verified": False, "message": "Telegram user ID is missing."}), 400

    channel = clean(os.getenv("TELEGRAM_CHANNEL", "@mptendersalert"))
    try:
        member = telegram_api("getChatMember", {"chat_id": channel, "user_id": user_id})
    except Exception as exc:
        return jsonify({"verified": False, "message": "Membership check unavailable. Bot must be administrator of the channel.", "error": str(exc)}), 503

    if not member.get("ok"):
        return jsonify({"verified": False, "message": "Membership check failed. Please join the Telegram channel first."}), 403

    status = member.get("result", {}).get("status", "")
    is_member = bool(member.get("result", {}).get("is_member", False))
    allowed = status in {"creator", "administrator", "member"} or (status == "restricted" and is_member)

    if not allowed:
        return jsonify({"verified": False, "message": "You are not a member of the Telegram channel. Please join it first."}), 403

    # Use the Login Widget photo when available; otherwise fetch the
    # latest Telegram profile photo through the Bot API.
    photo_url = clean(payload.get("photo_url"))
    if not photo_url:
        photo_url = telegram_profile_photo_url(user_id)

    return jsonify({
        "verified": True,
        "id": user_id,
        "username": payload.get("username", ""),
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "photo_url": photo_url,
        "message": "Telegram membership verified."
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
