import sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import server

class UserExportTests(unittest.TestCase):
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
