import sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import server

class UserExportTests(unittest.TestCase):
    def test_visitor_export_uses_saved_verification_instead_of_hardcoded_no(self):
        visitors=[{'mobile':'+919876543210'},{'mobile':'+919876543211'},{'mobile':'+919876543210'}]
        def account(mobile):return {'mobile_verified':mobile=='+919876543210'}
        with patch.object(server.account_service,'get',side_effect=account) as lookup:
            self.assertEqual(server.visitor_verification_flags(visitors),{'+919876543210':True,'+919876543211':False})
            self.assertEqual(lookup.call_count,2)

    def test_private_migration_requires_google_admin_and_includes_login_history(self):
        client=server.app.test_client()
        with patch.object(server,"user_db") as connect:
            self.assertEqual(client.get('/api/admin/users-migration').status_code,401)
            connect.assert_not_called()
        import sqlite3,tempfile
        with tempfile.TemporaryDirectory() as temporary, patch.object(server,"DATABASE_URL",""), patch.object(server,"USER_DB_PATH",Path(temporary)/'users.db'):
            server.touch_user_login(123,{"first_name":"Test"})
            with patch.object(server,"read_admin_session",return_value="allowed@example.test"):
                response=client.get('/api/admin/users-migration',headers={"Authorization":"Bearer valid"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['users'][0]['telegram_id'],123)
            self.assertEqual(len(response.json['login_events']),1)
            self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_export_requires_admin_and_supports_verified_google_session(self):
        client=server.app.test_client()
        with patch.object(server,"build_user_excel",return_value=b"xlsx") as build, patch.object(server,"telegram_user_from_session",return_value=None):
            self.assertEqual(client.post("/api/users/export",json={}).status_code,403)
            build.assert_not_called()
            with patch.object(server,"read_admin_session",return_value="allowed@example.test"):
                response=client.post("/api/users/export",json={},headers={"Authorization":"Bearer valid"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.data,b"xlsx")
            self.assertIn("MP_Tender_Users_Report.xlsx",response.headers["Content-Disposition"])
        with patch.object(server,"read_admin_session",return_value=None), patch.object(server,"telegram_user_from_session",return_value=123), patch.object(server,"is_user_admin",return_value=True), patch.object(server,"build_user_excel",return_value=b"xlsx"):
            self.assertEqual(client.post("/api/users/export",json={}).status_code,200)
