import importlib.util
import sqlite3
import unittest
from pathlib import Path
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_to_d1", root / "export_to_d1.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

class MigrationTests(unittest.TestCase):
    def test_import_preserves_profiles_history_ids_and_quotes_without_overwriting(self):
        snapshot = {"version": 1, "users": [{"telegram_id": 123, "name": "O'Brien नाम", "mobile": "+919876543210", "email": "a@example.test", "signup_at": "2026-09-30T10:00:00+05:30", "last_login_at": "2026-09-30T11:00:00+05:30", "login_count": 3}], "login_events": [{"id": 17, "telegram_id": 123, "login_at": "2026-09-30T11:00:00+05:30"}]}
        db = sqlite3.connect(":memory:")
        db.executescript((root / "migrations/0001_users.sql").read_text())
        sql = migration.build_sql(snapshot)
        db.executescript(sql)
        self.assertEqual(db.execute("SELECT name,login_count FROM users").fetchone(), ("O'Brien नाम", 3))
        self.assertEqual(db.execute("SELECT id FROM login_events").fetchone()[0], 17)
        db.execute("INSERT INTO login_events(telegram_id,login_at) VALUES(123,'next')")
        self.assertEqual(db.execute("SELECT MAX(id) FROM login_events").fetchone()[0], 18)
        with self.assertRaises(sqlite3.IntegrityError): db.executescript(sql)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
