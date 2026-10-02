import sys, tempfile, unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import server


class AdminStatsTests(unittest.TestCase):
    def test_unique_mobile_users_and_ist_day_boundaries(self):
        rows = [
            {'visitor_id': 'old-device', 'mobile': '+919876543210', 'signup_at': '2026-09-30T09:00:00+05:30', 'last_visit_at': '2026-10-02T10:00:00+05:30'},
            {'visitor_id': 'new-device', 'mobile': '9876543210', 'signup_at': '2026-10-02T11:00:00+05:30', 'last_visit_at': '2026-10-02T11:00:00+05:30'},
            {'visitor_id': 'new-user', 'mobile': '+919123456789', 'signup_at': '2026-10-01T18:30:00Z', 'last_visit_at': '2026-10-01T18:30:00Z'},
            {'visitor_id': 'inactive', 'mobile': '+916000000000', 'signup_at': '2026-10-01T18:29:59Z', 'last_visit_at': '2026-10-01T18:29:59Z'},
            {'visitor_id': 'bad-date', 'mobile': '+917000000000', 'signup_at': 'invalid', 'last_visit_at': ''},
            {'visitor_id': 'tomorrow', 'mobile': '+918000000000', 'signup_at': '2026-10-02T18:30:00Z', 'last_visit_at': '2026-10-02T18:30:00Z'},
        ]
        stats = server.dashboard_user_counts(rows, datetime.fromisoformat('2026-10-02T17:00:00+05:30'))
        self.assertEqual(stats['total_users'], 5)
        self.assertEqual(stats['today_signups'], 1)
        self.assertEqual(stats['today_active_users'], 2)
        self.assertEqual(stats['today_returning_users'], 1)
        self.assertEqual(stats['date_ist'], '2026-10-02')
        self.assertNotIn('mobile', str(stats))

    def test_admin_required_before_any_storage_read(self):
        with patch.object(server.sheet_store, 'call') as remote, patch.object(server, 'user_db') as db:
            response = server.app.test_client().get('/api/admin/stats')
            self.assertEqual(response.status_code, 401)
            remote.assert_not_called(); db.assert_not_called()

    def test_current_sheet_operation_supported_and_no_private_records_exposed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'USER_DB_PATH', Path(tmp)/'users.db'), patch.object(server, 'read_admin_session', return_value='admin@example.test'), patch.object(server.sheet_store, 'enabled', return_value=True), patch.object(server.sheet_store, 'call', return_value={'visitors': [{'visitor_id': 'test', 'mobile': '+919876543210', 'name': 'Private name', 'signup_at': '2026-10-02T10:00:00+05:30', 'last_visit_at': '2026-10-02T10:00:00+05:30', 'visit_count': 99}]}) as remote, patch.object(server, 'now_ist', return_value=datetime.fromisoformat('2026-10-02T17:00:00+05:30')):
            response = server.app.test_client().get('/api/admin/stats', headers={'Authorization': 'Bearer test'})
            self.assertEqual(response.status_code, 200)
            remote.assert_called_once_with('list_visitors')
            self.assertEqual(response.json['stats']['total_users'], 1)
            self.assertEqual(response.json['stats']['today_active_users'], 1)
            self.assertNotIn('9876543210', str(response.json))
            self.assertNotIn('Private name', str(response.json))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_sqlite_and_telegram_profiles_merge_by_mobile(self):
        now = datetime.fromisoformat('2026-10-02T17:00:00+05:30')
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'USER_DB_PATH', Path(tmp)/'users.db'), patch.object(server, 'read_admin_session', return_value='admin@example.test'), patch.object(server.sheet_store, 'enabled', return_value=False), patch.object(server, 'now_ist', return_value=now):
            server.touch_user_login(100, {'first_name': 'Test'})
            conn = server.visitor_db()
            conn.execute('UPDATE users SET mobile=? WHERE telegram_id=?', ('+919876543210',100))
            conn.execute('INSERT INTO visitor_registrations (visitor_id,name,mobile,district,signup_at,last_visit_at,visit_count) VALUES (?,?,?,?,?,?,?)', ('one','Test','9876543210','Dewas',now.isoformat(),now.isoformat(),80))
            conn.commit(); conn.close()
            response = server.app.test_client().get('/api/admin/stats', headers={'Authorization':'Bearer test'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['stats']['total_users'], 1)
            self.assertEqual(response.json['stats']['today_signups'], 1)
            self.assertEqual(response.json['stats']['today_active_users'], 1)

    def test_storage_error_is_not_reported_as_zero_users(self):
        with patch.object(server, 'read_admin_session', return_value='admin@example.test'), patch.object(server.sheet_store, 'enabled', return_value=True), patch.object(server.sheet_store, 'call', side_effect=server.sheet_store.SheetStoreError('Unavailable')):
            response = server.app.test_client().get('/api/admin/stats', headers={'Authorization':'Bearer test'})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('stats', response.json)


if __name__ == '__main__':
    unittest.main()
