import csv
import os
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request as urllib_request, parse as urllib_parse
import subprocess
import tempfile

from flask import Flask, jsonify, request, Response
from flask_cors import CORS

from scraper import scrape_mp_tenders
import google_sheet_store as sheet_store

app = Flask(__name__)
CORS(app)

@app.errorhandler(sheet_store.SheetStoreError)
def sheet_store_error(error):
    return jsonify({"ok":False,"message":str(error)}),error.status

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


# ================= USER REGISTRATION / ANALYTICS =================
# Telegram Login does not expose a user's phone number. We therefore ask the
# user for their name + mobile number after the first verified Telegram login.
# Data is stored in SQLite. Set USER_DB_PATH to a directory/file on a Render
# persistent disk for durable storage (for example /var/data/users.db).
USER_DB_PATH = Path(os.getenv("USER_DB_PATH", str(ROOT / "data" / "users.db")))
USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = clean(os.getenv("DATABASE_URL"))

class _DBRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class _DBCursor:
    def __init__(self, cursor):
        self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return _DBRow(row)
        try:
            return _DBRow(dict(row))
        except Exception:
            return row
    def fetchall(self):
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_DBRow(row))
            else:
                try:
                    out.append(_DBRow(dict(row)))
                except Exception:
                    out.append(row)
        return out

class _DBConnection:
    def __init__(self, conn, postgres=False):
        self.conn = conn
        self.postgres = postgres
    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return _DBCursor(self.conn.execute(sql, params))
    def executescript(self, script):
        if self.postgres:
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    self.conn.execute(statement)
        else:
            self.conn.executescript(script)
    def commit(self):
        self.conn.commit()
    def close(self):
        self.conn.close()

IST = timezone(timedelta(hours=5, minutes=30))

def user_db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)
        conn = _DBConnection(raw, postgres=True)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                mobile TEXT NOT NULL DEFAULT '',
                mobile_verified INTEGER NOT NULL DEFAULT 0,
                signup_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL,
                login_count INTEGER NOT NULL DEFAULT 0,
                email TEXT NOT NULL DEFAULT '',
                district TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS login_events (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                login_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_events_time ON login_events(login_at);
        """)
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS district TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT ''")
        conn.commit()
        return conn

    import sqlite3
    raw = sqlite3.connect(str(USER_DB_PATH), timeout=30)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA busy_timeout=30000")
    conn = _DBConnection(raw, postgres=False)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            mobile TEXT NOT NULL DEFAULT '',
            mobile_verified INTEGER NOT NULL DEFAULT 0,
            signup_at TEXT NOT NULL,
            last_login_at TEXT NOT NULL,
            login_count INTEGER NOT NULL DEFAULT 0,
            email TEXT NOT NULL DEFAULT '',
            district TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS login_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            login_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_login_events_time
            ON login_events(login_at);
    """)
    existing = {row[1] for row in raw.execute("PRAGMA table_info(users)").fetchall()}
    if "email" not in existing:
        raw.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "district" not in existing:
        raw.execute("ALTER TABLE users ADD COLUMN district TEXT NOT NULL DEFAULT ''")
    if "state" not in existing:
        raw.execute("ALTER TABLE users ADD COLUMN state TEXT NOT NULL DEFAULT ''")
    raw.commit()
    return conn

def user_db_backend():
    return "postgres" if DATABASE_URL else "sqlite"

def now_ist():
    return datetime.now(IST)

def normalise_mobile(value):
    digits = "".join(ch for ch in clean(value) if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10 or digits[0] not in "6789":
        return ""
    return "+91" + digits

def admin_telegram_ids():
    raw = clean(os.getenv("ADMIN_TELEGRAM_IDS", ""))
    ids = set()
    for item in raw.split(","):
        item = clean(item)
        if item.isdigit():
            ids.add(int(item))
    return ids

def is_user_admin(user_id):
    if int(user_id or 0) in admin_telegram_ids():
        return True
    # The channel owner (creator) may access the user report without another
    # secret being placed in the frontend.
    try:
        channel = clean(os.getenv("TELEGRAM_CHANNEL", "@mptendersalert"))
        member = telegram_api("getChatMember", {"chat_id": channel, "user_id": int(user_id)})
        return bool(member.get("ok")) and member.get("result", {}).get("status") == "creator"
    except Exception:
        return False

def touch_user_login(user_id, telegram_payload=None):
    telegram_payload = telegram_payload or {}
    uid = int(user_id)
    now = now_ist().isoformat()
    first_name = clean(telegram_payload.get("first_name"))
    last_name = clean(telegram_payload.get("last_name"))
    username = clean(telegram_payload.get("username"))
    display_name = " ".join(x for x in (first_name, last_name) if x).strip()

    conn = user_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()
        if row is None:
            conn.execute("""
                INSERT INTO users
                (telegram_id, first_name, last_name, username, name, mobile,
                 mobile_verified, signup_at, last_login_at, login_count)
                VALUES (?, ?, ?, ?, ?, '', 0, ?, ?, 1)
            """, (uid, first_name, last_name, username, display_name, now, now))
        else:
            conn.execute("""
                UPDATE users
                SET first_name=COALESCE(NULLIF(?, ''), first_name),
                    last_name=COALESCE(NULLIF(?, ''), last_name),
                    username=COALESCE(NULLIF(?, ''), username),
                    last_login_at=?,
                    login_count=login_count+1
                WHERE telegram_id=?
            """, (first_name, last_name, username, now, uid))
        conn.execute(
            "INSERT INTO login_events (telegram_id, login_at) VALUES (?, ?)",
            (uid, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()
        return dict(row)
    finally:
        conn.close()

def get_user(user_id):
    conn = user_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (int(user_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_user_profile(user_id, name, mobile, email="", state="", district=""):
    import re
    name = clean(name)
    mobile = normalise_mobile(mobile)
    email = clean(email).lower()
    state = clean(state)
    district = clean(district)
    if len(name) < 2:
        raise ValueError("कृपया अपना पूरा नाम दर्ज करें।")
    if not mobile:
        raise ValueError("कृपया सही नंबर डालिए।")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", email):
        raise ValueError("कृपया सही ईमेल आईडी डालिए।")
    allowed_domains = {"gmail.com", "yahoo.com", "yahoo.co.in", "rediffmail.com"}
    local, domain = email.rsplit("@", 1) if "@" in email else ("", "")
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+", local) or not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", domain):
        raise ValueError("कृपया सही ईमेल आईडी डालिए।")
    if not state:
        raise ValueError("कृपया राज्य चुनें।")
    if len(district) < 2:
        raise ValueError("कृपया जिला चुनें।")
    conn = user_db()
    try:
        conn.execute("""
            UPDATE users
            SET name=?, mobile=?, email=?, state=?, district=?, mobile_verified=0
            WHERE telegram_id=?
        """, (name, mobile, email, state, district, int(user_id)))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (int(user_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def user_stats():
    conn = user_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        registered_mobile = conn.execute(
            "SELECT COUNT(*) FROM users WHERE mobile<>''"
        ).fetchone()[0]
        today = now_ist().date().isoformat()
        today_new = conn.execute(
            "SELECT COUNT(*) FROM users WHERE substr(signup_at,1,10)=?", (today,)
        ).fetchone()[0]
        today_users = conn.execute(
            "SELECT COUNT(DISTINCT telegram_id) FROM login_events WHERE substr(login_at,1,10)=?",
            (today,),
        ).fetchone()[0]
        today_logins = conn.execute(
            "SELECT COUNT(*) FROM login_events WHERE substr(login_at,1,10)=?",
            (today,),
        ).fetchone()[0]
        daily_rows = conn.execute("""
            SELECT substr(signup_at,1,10) AS day, COUNT(*) AS new_users
            FROM users
            GROUP BY substr(signup_at,1,10)
            ORDER BY day DESC
            LIMIT 90
        """).fetchall()
        return {
            "total_users": total,
            "registered_mobile": registered_mobile,
            "today_new_users": today_new,
            "today_unique_users": today_users,
            "today_logins": today_logins,
            "daily_signups": [dict(r) for r in daily_rows],
        }
    finally:
        conn.close()

def build_user_excel():
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    conn = visitor_db()
    try:
        users = conn.execute("""
            SELECT telegram_id, name, mobile, email, state, district, username, first_name, last_name,
                   signup_at, last_login_at, login_count, mobile_verified
            FROM users
            ORDER BY signup_at DESC
        """).fetchall()
        visitors = (sheet_store.call('list_visitors')['visitors'] if sheet_store.enabled()
                    else conn.execute("SELECT * FROM visitor_registrations ORDER BY signup_at DESC").fetchall())
        daily = conn.execute("""
            SELECT substr(signup_at,1,10) AS day,
                   COUNT(*) AS new_users,
                   (SELECT COUNT(DISTINCT le.telegram_id)
                    FROM login_events le
                    WHERE substr(le.login_at,1,10)=substr(u.signup_at,1,10)) AS unique_logins,
                   (SELECT COUNT(*)
                    FROM login_events le2
                    WHERE substr(le2.login_at,1,10)=substr(u.signup_at,1,10)) AS total_logins
            FROM users u
            GROUP BY substr(signup_at,1,10)
            ORDER BY day DESC
        """).fetchall()
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Users"
    headers = [
        "S.No.", "Name", "Mobile", "Email", "State", "District", "Telegram Username", "Telegram ID",
        "First Signup (IST)", "Last Login (IST)", "Login Count",
        "Mobile Verified"
    ]
    ws.append(headers)
    for i, row in enumerate(users, 1):
        ws.append([
            i, row["name"], row["mobile"], row["email"], row["state"], row["district"], ("@" + row["username"]) if row["username"] else "",
            row["telegram_id"], row["signup_at"], row["last_login_at"],
            row["login_count"], "Yes" if row["mobile_verified"] else "No"
        ])
    for cell in ws[1]:
        cell.font = cell.font.copy(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    ds = wb.create_sheet("Daily Stats")
    ds.append(["Date (IST)", "New Users", "Unique Users", "Total Logins"])
    for row in daily:
        ds.append([row["day"], row["new_users"], row["unique_logins"], row["total_logins"]])
    for cell in ds[1]:
        cell.font = cell.font.copy(bold=True)
    ds.freeze_panes = "A2"

    vs = wb.create_sheet("Visitor Registrations")
    vs.append(["S.No.", "Name", "Mobile", "District", "First Saved (IST)", "Last Visit (IST)", "Visit Count", "Mobile Verified", "First Name", "Middle Name", "Last Name", "Gender"])
    for i, row in enumerate(visitors, 1):
        vs.append([i,row["name"],row["mobile"],row["district"],row["signup_at"],row["last_visit_at"],row["visit_count"],dict(row).get("mobile_verified") or "No",*[dict(row).get(key,"") for key in ("first_name","middle_name","last_name","gender")]])
    for row in vs.iter_rows(min_row=2):
        for cell in row[1:4]:
            cell.data_type = "s"
    for cell in vs[1]:
        cell.font = cell.font.copy(bold=True)
    vs.freeze_panes = "A2"
    vs.auto_filter.ref = vs.dimensions
    for sheet in (ws, ds, vs):
        for col in range(1, sheet.max_column + 1):
            max_len = max(
                len(str(sheet.cell(row=r, column=col).value or ""))
                for r in range(1, min(sheet.max_row, 200) + 1)
            )
            sheet.column_dimensions[get_column_letter(col)].width = min(max(max_len + 2, 12), 34)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.read()

# Temporary public access: collect a profile without Telegram authentication.
def visitor_db():
    conn = user_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS visitor_registrations (
            visitor_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mobile TEXT NOT NULL,
            district TEXT NOT NULL,
            signup_at TEXT NOT NULL,
            last_visit_at TEXT NOT NULL,
            visit_count INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS visitor_events (
            event_id TEXT PRIMARY KEY,
            visitor_id TEXT NOT NULL,
            visited_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS visitor_affidavit_profiles (
            visitor_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def make_visitor_session(visitor_id):
    import base64
    secret = clean(os.getenv("VISITOR_SESSION_SECRET")) or admin_session_secret()
    if not secret:
        raise RuntimeError("Visitor session signing unavailable")
    payload = {"visitor_id": visitor_id, "exp": int((now_ist()+timedelta(days=365)).timestamp())}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), ("visitor:"+body).encode(), hashlib.sha256).hexdigest()
    return body+"."+signature


def read_visitor_session(token):
    import base64
    try:
        body, signature = clean(token).split(".", 1)
        secret = clean(os.getenv("VISITOR_SESSION_SECRET")) or admin_session_secret()
        if not secret or len(body)>1024:
            return None
        expected = hmac.new(secret.encode(), ("visitor:"+body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body+"="*((-len(body))%4)))
        if int(payload["exp"])<=int(now_ist().timestamp()):
            return None
        return str(payload["visitor_id"])
    except (ValueError, KeyError, TypeError):
        return None


def visitor_response(row, affidavit_profile=None):
    response = jsonify({"ok": True, "registered": True, "visitor_id": row["visitor_id"],
                        "session_token": make_visitor_session(row["visitor_id"]),
                        "storage": "google_sheets" if sheet_store.enabled() else user_db_backend(),
                        "affidavit_profile": affidavit_profile or {},
                        "profile": {key:row[key] for key in ("name", "mobile", "district")}})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/visitors/register")
def visitors_register():
    if os.getenv('VISITOR_PROFILE_LEGACY_ALLOWED','0')!='1':
        return jsonify({'ok':False,'message':'Please use Sign up with a password.'}),410
    import uuid
    payload = request.get_json(silent=True) or {}
    name, district = clean(payload.get("name")), clean(payload.get("district"))
    mobile = normalise_mobile(payload.get("mobile"))
    if not 2<=len(name)<=120 or not mobile or not 2<=len(district)<=100:
        return jsonify({"ok":False,"message":"सही Name, 10-digit Mobile Number और District भरें।"}),400
    try:
        visitor_id = str(uuid.UUID(clean(payload.get("registration_id"))))
    except (ValueError, AttributeError):
        return jsonify({"ok":False,"message":"Registration request invalid. Reload and retry."}),400
    # A visitor token grants public profile access only; never admin privileges.
    make_visitor_session(visitor_id)
    if sheet_store.enabled():
        result=sheet_store.call('register',visitor_id=visitor_id,name=name,mobile=mobile,district=district)
        return visitor_response(result['visitor'],result.get('affidavit_profile'))
    conn = visitor_db()
    try:
        now = now_ist().isoformat()
        inserted = conn.execute("""INSERT INTO visitor_registrations
            (visitor_id,name,mobile,district,signup_at,last_visit_at,visit_count)
            VALUES (?,?,?,?,?,?,1) ON CONFLICT (visitor_id) DO NOTHING""",
            (visitor_id,name,mobile,district,now,now))
        if inserted.cursor.rowcount == 1:
            conn.execute("INSERT INTO visitor_events (event_id,visitor_id,visited_at) VALUES (?,?,?)",(str(uuid.uuid4()),visitor_id,now))
        row = dict(conn.execute("SELECT * FROM visitor_registrations WHERE visitor_id=?",(visitor_id,)).fetchone())
        if (row["name"],row["mobile"],row["district"])!=(name,mobile,district):
            return jsonify({"ok":False,"message":"Registration request already used. Reload and retry."}),409
        conn.commit()
        return visitor_response(row)
    finally:
        conn.close()


@app.post("/api/visitors/session")
def visitors_session():
    if os.getenv('VISITOR_PROFILE_LEGACY_ALLOWED','0')!='1':
        return jsonify({'ok':False,'message':'Please use mobile/password Sign in.'}),401
    import uuid
    payload = request.get_json(silent=True) or {}
    visitor_id = read_visitor_session(payload.get("session_token"))
    if not visitor_id:
        return jsonify({"ok":False,"message":"Please save your profile again."}),401
    if sheet_store.enabled():
        result=sheet_store.call('session',visitor_id=visitor_id)
        return visitor_response(result['visitor'],result.get('affidavit_profile'))
    conn = visitor_db()
    try:
        row = conn.execute("SELECT * FROM visitor_registrations WHERE visitor_id=?",(visitor_id,)).fetchone()
        if not row:
            return jsonify({"ok":False,"message":"Profile not found. Please save again."}),401
        now = now_ist().isoformat()
        conn.execute("UPDATE visitor_registrations SET last_visit_at=?,visit_count=visit_count+1 WHERE visitor_id=?",(now,visitor_id))
        conn.execute("INSERT INTO visitor_events (event_id,visitor_id,visited_at) VALUES (?,?,?)",(str(uuid.uuid4()),visitor_id,now))
        conn.commit()
        profile=conn.execute('SELECT profile_json FROM visitor_affidavit_profiles WHERE visitor_id=?',(visitor_id,)).fetchone()
        return visitor_response(dict(row),json.loads(profile['profile_json']) if profile else {})
    finally:
        conn.close()


@app.get("/api/admin/visitor-registrations")
def admin_visitor_registrations():
    email, _ = require_admin()
    if not email:
        return jsonify({"ok":False,"message":"Admin access required."}),401
    if sheet_store.enabled():
        response=jsonify({"ok":True,"visitors":sheet_store.call('list_visitors')['visitors']})
        response.headers['Cache-Control']='no-store'
        return response
    conn = visitor_db()
    try:
        rows = conn.execute("SELECT * FROM visitor_registrations ORDER BY signup_at DESC").fetchall()
        response = jsonify({"ok":True,"visitors":[dict(row) for row in rows]})
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        conn.close()


@app.post('/api/visitors/affidavit')
def visitor_affidavit():
    payload=request.get_json(silent=True) or {}
    token=payload.get('session_token')
    if not (isinstance(token,str) and token.startswith('acct_')) and os.getenv('VISITOR_PROFILE_LEGACY_ALLOWED','0')!='1':
        return jsonify({'ok':False,'message':'Mobile/password account session required.'}),401
    visitor_id=(account_service.authenticate(token)['user_id'] if isinstance(token,str) and token.startswith('acct_') else read_visitor_session(token))
    if not visitor_id:
        return jsonify({'ok':False,'message':'Saved profile session required.'}),401
    profile=payload.get('profile')
    if profile is not None:
        keys=('bidderName','firmName','status','place','relative','relativeName','relativePost','relativePosting')
        if not isinstance(profile,dict):
            return jsonify({'ok':False,'message':'Invalid affidavit profile.'}),400
        profile={key:clean(profile.get(key)) for key in keys}
        if (any(len(value)>240 for value in profile.values())
            or not all(profile[key] for key in ('bidderName','firmName','status','place'))
            or profile['relative'] not in ('yes','no')
            or (profile['relative']=='yes' and not all(profile[key] for key in ('relativeName','relativePost','relativePosting')))):
            return jsonify({'ok':False,'message':'Required affidavit basic details are missing or invalid.'}),400
    if sheet_store.enabled():
        result=sheet_store.call('read_affidavit' if profile is None else 'save_affidavit',visitor_id=visitor_id,**({} if profile is None else {'profile':profile}))
        response=jsonify({'ok':True,'profile':result['profile'],'storage':'google_sheets'})
        response.headers['Cache-Control']='no-store'
        return response
    conn=visitor_db()
    try:
        if not conn.execute('SELECT visitor_id FROM visitor_registrations WHERE visitor_id=?',(visitor_id,)).fetchone():
            return jsonify({'ok':False,'message':'Profile not found. Please save again.'}),401
        if profile is not None:
            conn.execute('''INSERT INTO visitor_affidavit_profiles (visitor_id,profile_json,updated_at)
                VALUES (?,?,?) ON CONFLICT(visitor_id) DO UPDATE SET
                profile_json=excluded.profile_json,updated_at=excluded.updated_at''',
                (visitor_id,json.dumps(profile,ensure_ascii=False),now_ist().isoformat()))
            conn.commit()
        row=conn.execute('SELECT profile_json FROM visitor_affidavit_profiles WHERE visitor_id=?',(visitor_id,)).fetchone()
        response=jsonify({'ok':True,'profile':json.loads(row['profile_json']) if row else {},'storage':user_db_backend()})
        response.headers['Cache-Control']='no-store'
        return response
    finally:
        conn.close()


@app.get('/api/admin/profile-storage')
def admin_profile_storage():
    email,_=require_admin()
    if not email:
        return jsonify({'ok':False,'message':'Admin access required.'}),401
    result=sheet_store.call('status') if sheet_store.enabled() else {}
    response=jsonify({'ok':True,'storage':'google_sheets' if sheet_store.enabled() else user_db_backend(),**result})
    response.headers['Cache-Control']='no-store'
    return response


def telegram_user_from_session(payload):
    token = clean((payload or {}).get("session_token"))
    return read_telegram_session(token)


TELEGRAM_SESSION_TTL = int(os.getenv("TELEGRAM_SESSION_TTL", "604800"))  # 7 days

def make_telegram_session(user_id):
    import base64, time
    payload = {"uid": int(user_id), "exp": int(time.time()) + TELEGRAM_SESSION_TTL}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    secret = clean(os.getenv("TELEGRAM_SESSION_SECRET")) or clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return body + "." + sig

def read_telegram_session(token):
    import base64, time
    if not token or "." not in token: return None
    body, received_sig = token.rsplit(".", 1)
    secret = clean(os.getenv("TELEGRAM_SESSION_SECRET")) or clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    expected_sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, received_sig): return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()): return None
        user_id = int(payload.get("uid", 0))
        return user_id or None
    except Exception:
        return None


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

    # Create/update the user record and record this login event.
    user_record = touch_user_login(user_id, payload)

    # Use the Login Widget photo when available; otherwise fetch the
    # latest Telegram profile photo through the Bot API.
    photo_url = clean(payload.get("photo_url"))
    if not photo_url:
        photo_url = telegram_profile_photo_url(user_id)

    session_token = make_telegram_session(user_id)
    return jsonify({
        "verified": True,
        "session_token": session_token,
        "id": user_id,
        "username": payload.get("username", ""),
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "photo_url": photo_url,
        "profile_registered": bool(user_record.get("name") and user_record.get("mobile") and user_record.get("email") and user_record.get("state") and user_record.get("district")),
        "message": "Telegram membership verified."
    })


@app.post("/api/telegram/session")
def telegram_session():
    payload = request.get_json(silent=True) or {}
    user_id = read_telegram_session(clean(payload.get("session_token")))
    if not user_id:
        return jsonify({"verified": False, "message": "Telegram session expired. Please login again."}), 401
    channel = clean(os.getenv("TELEGRAM_CHANNEL", "@mptendersalert"))
    try:
        member = telegram_api("getChatMember", {"chat_id": channel, "user_id": user_id})
    except Exception as exc:
        return jsonify({"verified": False, "message": "Membership check unavailable.", "error": str(exc)}), 503
    if not member.get("ok"):
        return jsonify({"verified": False, "message": "Membership check failed."}), 403
    status = member.get("result", {}).get("status", "")
    is_member = bool(member.get("result", {}).get("is_member", False))
    allowed = status in {"creator", "administrator", "member"} or (status == "restricted" and is_member)
    if not allowed:
        return jsonify({"verified": False, "message": "Telegram channel membership is no longer active."}), 403
    user_record = touch_user_login(user_id)
    return jsonify({"verified": True, "id": user_id, "session_token": make_telegram_session(user_id), "profile_registered": bool(user_record.get("name") and user_record.get("mobile") and user_record.get("email") and user_record.get("state") and user_record.get("district")), "is_admin": is_user_admin(user_id), "message": "Telegram session verified."})


@app.get("/api/users/profile")
def users_profile():
    user_id = telegram_user_from_session(request.args)
    if not user_id:
        return jsonify({"ok": False, "message": "Valid Telegram session required."}), 401
    row = get_user(user_id)
    if not row:
        return jsonify({"ok": True, "registered": False, "is_admin": is_user_admin(user_id)})
    return jsonify({
        "ok": True,
        "registered": bool(row.get("name") and row.get("mobile") and row.get("email") and row.get("state") and row.get("district")),
        "is_admin": is_user_admin(user_id),
        "user": {
            "name": row.get("name", ""),
            "mobile": row.get("mobile", ""),
            "email": row.get("email", ""),
            "state": row.get("state", ""),
            "district": row.get("district", ""),
            "username": row.get("username", ""),
            "telegram_id": row.get("telegram_id"),
        }
    })

@app.post("/api/users/register")
def users_register():
    payload = request.get_json(silent=True) or {}
    user_id = telegram_user_from_session(payload)
    if not user_id:
        return jsonify({"ok": False, "message": "Valid Telegram session required."}), 401
    try:
        row = update_user_profile(user_id, payload.get("name", ""), payload.get("mobile", ""), payload.get("email", ""), payload.get("state", ""), payload.get("district", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    if not row:
        return jsonify({"ok": False, "message": "User record was not found. Please login again."}), 404
    return jsonify({
        "ok": True,
        "registered": True,
        "message": "Profile saved successfully.",
        "user": {
            "name": row.get("name", ""),
            "mobile": row.get("mobile", ""),
        }
    })

@app.get("/api/users/list")
def users_list():
    user_id = telegram_user_from_session(request.args)
    if not user_id or not is_user_admin(user_id):
        return jsonify({"ok": False, "message": "Admin access required."}), 403
    conn = user_db()
    try:
        rows = conn.execute("""
            SELECT name, mobile, email, state, district, username, telegram_id, signup_at, last_login_at, login_count
            FROM users ORDER BY signup_at DESC
        """).fetchall()
        return jsonify({"ok": True, "users": [dict(row) for row in rows]})
    finally:
        conn.close()

@app.get("/api/users/stats")
def users_stats():
    user_id = telegram_user_from_session(request.args)
    if not user_id or not is_user_admin(user_id):
        return jsonify({"ok": False, "message": "Admin access required."}), 403
    return jsonify({"ok": True, **user_stats()})

@app.post("/api/users/export")
def users_export():
    # /admin uses the verified Google admin session; retain Telegram admin
    # access for the dashboard's existing export option.
    email, _ = require_admin()
    payload = request.get_json(silent=True) or {}
    user_id = telegram_user_from_session(payload) if not email else None
    if not email and (not user_id or not is_user_admin(user_id)):
        return jsonify({"ok": False, "message": "Admin access required."}), 403
    data = build_user_excel()
    filename = "MP_Tender_Users_Report.xlsx"
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

def telegram_auth_valid(payload):
    received_hash = clean(payload.get("hash"))
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    if not received_hash or not token:
        return None, "Telegram authentication data is missing."
    try:
        auth_date = int(payload.get("auth_date", 0) or 0)
    except Exception:
        return None, "Invalid Telegram authentication date."
    now = int(datetime.now(timezone.utc).timestamp())
    if not auth_date or now - auth_date > 86400:
        return None, "Telegram verification expired. Please login again."
    check_fields = {k: str(v) for k, v in payload.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={check_fields[k]}" for k in sorted(check_fields))
    secret_key = hashlib.sha256(token.encode("utf-8")).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None, "Telegram authentication could not be validated."
    try:
        user_id = int(payload.get("id", 0) or 0)
    except Exception:
        user_id = 0
    if not user_id:
        return None, "Telegram user ID is missing."
    return user_id, ""

@app.post("/api/telegram/send-pdf")
def telegram_send_pdf():
    """Send a dashboard-generated PDF to the currently logged-in Telegram user."""
    payload = request.get_json(silent=True) or {}
    auth = payload.get("auth") or {}
    user_id, error = telegram_auth_valid(auth)
    if not user_id:
        return jsonify({"ok": False, "message": error or "Telegram login required."}), 401

    pdf_b64 = clean(payload.get("pdf_base64"))
    filename = clean(payload.get("filename")) or "MP_Tender_Dashboard.pdf"
    if not pdf_b64:
        return jsonify({"ok": False, "message": "PDF data is missing."}), 400
    try:
        import base64, requests
        pdf_bytes = base64.b64decode(pdf_b64, validate=True)
        if len(pdf_bytes) > 45 * 1024 * 1024:
            return jsonify({"ok": False, "message": "PDF is too large for this Telegram delivery."}), 413

        token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
        # The user must have opened/started the bot at least once; Telegram
        # does not allow a bot to initiate a brand-new private conversation.
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={
                "chat_id": str(user_id),
                "caption": "📄 MP Tender Dashboard PDF\nयह PDF आपके Telegram private chat में भेजी गई है।",
            },
            files={
                "document": (filename, pdf_bytes, "application/pdf"),
            },
            timeout=45,
        )
        result = response.json()
        if not result.get("ok"):
            description = clean(result.get("description")) or "Telegram PDF delivery failed."
            # Telegram cannot send the first private message until the user
            # has opened the bot and pressed START once.
            lower_description = description.lower()
            bot_not_started = any(text in lower_description for text in (
                "chat not found",
                "bot can't initiate conversation",
                "bot cannot initiate conversation",
                "user is deactivated",
                "forbidden"
            ))
            if bot_not_started:
                return jsonify({
                    "ok": False,
                    "bot_not_started": True,
                    "message": "Telegram bot को पहले START करना जरूरी है।"
                }), 409
            return jsonify({"ok": False, "message": description}), 502
        return jsonify({"ok": True, "message": "PDF Telegram पर भेज दी गई है।"})
    except Exception as exc:
        print(f"Telegram PDF send failed: {exc}")
        return jsonify({"ok": False, "message": "PDF Telegram पर भेजने में समस्या हुई। कृपया Telegram bot chat खोलकर Start दबाएँ।"}), 502


# ================= GOOGLE ADMIN CONTROL PANEL =================
ADMIN_EMAIL_ALLOWLIST = {
    x.strip().lower()
    for x in clean(os.getenv(
        "ADMIN_GOOGLE_EMAILS",
        "imriteshdhoot@gmail.com,shikhadhoot@gmail.com",
    )).split(",")
    if x.strip()
}
ADMIN_SESSION_TTL = int(os.getenv("ADMIN_SESSION_TTL", "28800"))  # 8 hours

def admin_session_secret():
    return clean(os.getenv("ADMIN_SESSION_SECRET")) or clean(os.getenv("TELEGRAM_SESSION_SECRET")) or clean(os.getenv("TELEGRAM_BOT_TOKEN"))

def make_admin_session(email):
    import base64, time
    payload = {"email": email.lower(), "exp": int(time.time()) + ADMIN_SESSION_TTL}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(admin_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return body + "." + sig

def read_admin_session(token):
    import base64, time
    if not token or "." not in token:
        return None
    body, received_sig = token.rsplit(".", 1)
    expected = hmac.new(admin_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_sig):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        email = clean(payload.get("email")).lower()
        return email if email in ADMIN_EMAIL_ALLOWLIST else None
    except Exception:
        return None

def require_admin():
    token = clean(request.headers.get("Authorization", "")).removeprefix("Bearer ").strip()
    email = read_admin_session(token)
    if not email:
        return None, (jsonify({"ok": False, "message": "Admin login required."}), 401)
    return email, None

@app.get("/api/admin/config")
def admin_config():
    return jsonify({
        "ok": True,
        "google_client_id": clean(os.getenv("GOOGLE_CLIENT_ID")),
        "allowed_domains": ["google.com"],
    })

@app.post("/api/admin/google")
def admin_google_login():
    payload = request.get_json(silent=True) or {}
    credential = clean(payload.get("credential"))
    if not credential:
        return jsonify({"ok": False, "message": "Google authentication token missing."}), 400
    client_id = clean(os.getenv("GOOGLE_CLIENT_ID"))
    if not client_id:
        return jsonify({"ok": False, "message": "Google Admin login is not configured on the server yet."}), 503

    # Verify the Google ID token server-side. The tokeninfo endpoint validates
    # the signature and exposes aud/email/email_verified for our checks.
    try:
        url = "https://oauth2.googleapis.com/tokeninfo?" + urllib_parse.urlencode({"id_token": credential})
        with urllib_request.urlopen(url, timeout=20) as response:
            info = json.loads(response.read().decode("utf-8"))
    except Exception:
        return jsonify({"ok": False, "message": "Google login verification failed."}), 401

    email = clean(info.get("email")).lower()
    if clean(info.get("aud")) != client_id:
        return jsonify({"ok": False, "message": "Google client verification failed."}), 401
    if str(info.get("email_verified", "")).lower() != "true":
        return jsonify({"ok": False, "message": "Google email is not verified."}), 403
    if email not in ADMIN_EMAIL_ALLOWLIST:
        return jsonify({"ok": False, "message": "इस Google account को Admin access नहीं दिया गया है।"}), 403

    return jsonify({
        "ok": True,
        "email": email,
        "session_token": make_admin_session(email),
        "expires_in": ADMIN_SESSION_TTL,
        "message": "Admin login successful.",
    })

@app.get("/api/admin/session")
def admin_session():
    email, error = require_admin()
    if error:
        return error
    return jsonify({"ok": True, "email": email})

@app.get("/api/admin/users-migration")
def users_migration_snapshot():
    """Private, consistent export for moving users and their login history."""
    email, error = require_admin()
    if error:
        return error
    conn = user_db()
    try:
        if conn.postgres:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        else:
            conn.execute("BEGIN")
        users = [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY telegram_id").fetchall()]
        events = [dict(row) for row in conn.execute("SELECT * FROM login_events ORDER BY id").fetchall()]
        return Response(json.dumps({"version": 1, "users": users, "login_events": events}),
                        mimetype="application/json", headers={"Cache-Control": "no-store",
                        "Content-Disposition": 'attachment; filename="mp-users-migration.private.json"'})
    finally:
        conn.close()

@app.post("/api/admin/logout")
def admin_logout():
    # Sessions are stateless and short-lived. Clearing the browser token is
    # sufficient; this endpoint exists for a clean client-side logout flow.
    return jsonify({"ok": True})

def run_manual_telegram_pdf(report, view="table"):
    """Send an admin PDF directly from Render using the latest committed live snapshot.

    This intentionally does not depend on GitHub workflow dispatch/PAT. The admin
    PDF buttons therefore keep working even when GITHUB_ACTIONS_TOKEN is absent.
    """
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = clean(os.getenv("TELEGRAM_CHAT_ID"))
    if not token or not chat_id:
        raise RuntimeError("Telegram configuration missing: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
    owner = clean(os.getenv("GITHUB_REPO_OWNER", "esign2015"))
    repo = clean(os.getenv("GITHUB_REPO_NAME", "mp-tenders"))
    branch = clean(os.getenv("GITHUB_REPO_BRANCH", "main"))
    source_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/organisation_tenders.csv"
    try:
        with urllib_request.urlopen(source_url, timeout=45) as response:
            csv_bytes = response.read()
    except Exception as exc:
        raise RuntimeError(f"Latest tender snapshot download failed: {type(exc).__name__}: {exc}") from exc
    if not csv_bytes or b"Tender ID" not in csv_bytes[:4096]:
        raise RuntimeError("Latest tender snapshot is empty or invalid; PDF was not sent.")
    with tempfile.TemporaryDirectory(prefix="mptender_admin_") as tmp:
        csv_path = Path(tmp) / "organisation_tenders.csv"
        csv_path.write_bytes(csv_bytes)
        env = os.environ.copy()
        env.update({"NOTIFY_MODE":"manual", "MANUAL_REPORT":report, "MANUAL_VIEW":view, "TENDER_CSV_PATH":str(csv_path)})
        proc = subprocess.run(
            [os.getenv("PYTHON", "python"), str(ROOT / "backend" / "telegram_alerts.py")],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240,
        )
        if proc.returncode != 0:
            detail = clean(proc.stderr) or clean(proc.stdout) or f"exit code {proc.returncode}"
            raise RuntimeError("Telegram PDF failed: " + detail[-1200:])
    return "Latest GitHub tender snapshot से Telegram PDF भेज दी गई है।"


def github_dispatch(workflow, inputs=None):
    token = clean(os.getenv("GITHUB_ACTIONS_TOKEN"))
    if not token:
        raise RuntimeError("GITHUB_ACTIONS_TOKEN is not configured on Render.")
    owner = clean(os.getenv("GITHUB_REPO_OWNER", "esign2015"))
    repo = clean(os.getenv("GITHUB_REPO_NAME", "mp-tenders"))
    body = {"ref": clean(os.getenv("GITHUB_REPO_BRANCH", "main"))}
    if inputs:
        body["inputs"] = inputs
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{urllib_parse.quote(workflow, safe='')}/dispatches"
    data = json.dumps(body).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "mp-tenders-admin",
        },
    )
    with urllib_request.urlopen(req, timeout=30) as response:
        if response.status not in (200, 201, 202, 204):
            raise RuntimeError(f"GitHub dispatch returned HTTP {response.status}")

def github_latest_workflow_run(workflow):
    token = clean(os.getenv("GITHUB_ACTIONS_TOKEN"))
    if not token:
        raise RuntimeError("GITHUB_ACTIONS_TOKEN is not configured on Render.")
    owner = clean(os.getenv("GITHUB_REPO_OWNER", "esign2015"))
    repo = clean(os.getenv("GITHUB_REPO_NAME", "mp-tenders"))
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{urllib_parse.quote(workflow, safe='')}/runs?event=workflow_dispatch&per_page=1"
    )
    req = urllib_request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mp-tenders-admin",
        },
    )
    with urllib_request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    runs = payload.get("workflow_runs") or []
    if not runs:
        return {}
    run = runs[0]
    return {
        "id": run.get("id"),
        "run_number": run.get("run_number"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "html_url": run.get("html_url"),
        "head_sha": run.get("head_sha"),
    }

@app.get("/api/admin/workflow-status")
def admin_workflow_status():
    email, error = require_admin()
    if error:
        return error
    action = clean(request.args.get("action")).lower()
    mapping = {
        "data_refresh": "scrape.yml",
        "retry_pending": "targeted-pending-retry.yml",
        "telegram_test": "telegram-test.yml",
        "closing_today": "telegram_manual_pdf.yml",
        "new_today": "telegram_manual_pdf.yml",
        "all": "telegram_manual_pdf.yml",
    }
    workflow = mapping.get(action)
    if not workflow:
        return jsonify({"ok": False, "message": "Unknown workflow status request."}), 400
    try:
        return jsonify({"ok": True, "action": action, "workflow": workflow, "run": github_latest_workflow_run(workflow), "requested_by": email})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 502


@app.post("/api/admin/action")
def admin_action():
    email, error = require_admin()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    action = clean(payload.get("action")).lower()
    try:
        if action == "telegram_pdf":
            report = clean(payload.get("report", "closing_today"))
            if report not in {"closing_today", "new_today", "all"}:
                return jsonify({"ok": False, "message": "Invalid PDF report."}), 400
            view = clean(payload.get("view", "table")).lower()
            if view not in {"table", "card"}:
                return jsonify({"ok": False, "message": "Invalid PDF view."}), 400
            github_dispatch("telegram_manual_pdf.yml", {"report": report, "view": view})
            message = f"Telegram {view.title()} PDF workflow started: {report}"
        elif action == "data_refresh":
            github_dispatch("scrape.yml")
            message = "Full data refresh workflow started."
        elif action == "retry_pending":
            github_dispatch("targeted-pending-retry.yml")
            message = "Pending detail retry workflow started."
        elif action == "telegram_test":
            github_dispatch("telegram-test.yml")
            message = "Telegram test workflow started."
        else:
            return jsonify({"ok": False, "message": "Unknown admin command."}), 400
        return jsonify({"ok": True, "message": message, "requested_by": email})
    except Exception as exc:
        print(f"Admin action failed: {exc}")
        return jsonify({"ok": False, "message": str(exc)}), 502

@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "csv_exists": CSV_FILE.exists(),
        "records": len(read_rows()),
        "user_db_backend": user_db_backend(),
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


import sys
from account_access import install as install_account_access
account_service=install_account_access(sys.modules[__name__])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
