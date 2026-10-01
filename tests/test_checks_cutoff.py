import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, Mock
sys.path.insert(0, "backend")
import checks_cutoff as cutoff
from stop_after_hours_checks import safe_to_cancel

class CutoffTests(unittest.TestCase):
    def test_ist_boundary_and_utc_equivalent(self):
        self.assertEqual(cutoff.seconds_left(datetime(2026,10,1,18,29,59,tzinfo=cutoff.IST)),1)
        self.assertEqual(cutoff.seconds_left(datetime(2026,10,1,13,0,tzinfo=timezone.utc)),0)
        self.assertEqual(cutoff.seconds_left(datetime(2026,10,1,23,59,tzinfo=cutoff.IST)),0)
    def test_after_hours_never_starts_child(self):
        with patch.object(cutoff,"seconds_left",return_value=0),patch.object(cutoff.subprocess,"Popen") as child:
            self.assertEqual(cutoff.main(),0)
            child.assert_not_called()
    def test_running_child_stops_at_deadline(self):
        child=Mock(pid=123)
        child.wait.side_effect=[cutoff.subprocess.TimeoutExpired("test",1),0]
        with patch.object(cutoff,"seconds_left",return_value=1),patch.object(cutoff.subprocess,"Popen",return_value=child),patch.object(cutoff.os,"killpg") as kill:
            self.assertEqual(cutoff.main(),0)
            child.wait.assert_any_call(timeout=1)
            kill.assert_called_once_with(123,cutoff.signal.SIGTERM)
    def test_full_snapshot_and_details_are_protected(self):
        self.assertTrue(safe_to_cancel([{"name":"corrigendum_watch","status":"in_progress"},{"name":"scrape_full","status":"completed"}]))
        for name in ("scrape_full","new_tender_detail_worker","monitor_changes"):
            self.assertFalse(safe_to_cancel([{"name":"corrigendum_urgent","status":"queued"},{"name":name,"status":"in_progress"}]))
        self.assertFalse(safe_to_cancel([]))
if __name__=="__main__":
    unittest.main()
