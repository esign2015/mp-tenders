import sys
import unittest
sys.path.insert(0, "backend")
from inventory_summary import REQUIRED_FIELDS, detail_complete
from portal_fee_exceptions import NOT_PROVIDED, VERIFIED_MISSING_PROCESSING_FEE

class VerifiedPortalFeeTests(unittest.TestCase):
    def row(self, tid):
        row = {key: "present" for key in REQUIRED_FIELDS}
        row.update({"Tender ID": tid, "Detail Extracted": "YES", "Pincode": "461228",
                    "Search Route": "Home -> Organisation -> Live Tender Link -> Detail",
                    "Processing Fee": NOT_PROVIDED})
        return row
    def test_verified_omissions_complete_without_fake_zero(self):
        for tid in VERIFIED_MISSING_PROCESSING_FEE:
            self.assertTrue(detail_complete(self.row(tid)))
    def test_unverified_omission_and_incomplete_fields_still_fail(self):
        self.assertFalse(detail_complete(self.row("2026_OTHER_123456_1")))
        row=self.row(next(iter(VERIFIED_MISSING_PROCESSING_FEE)))
        row["Pincode"]=""
        self.assertFalse(detail_complete(row))
    def test_positive_fee_remains_valid_and_zero_not_silently_accepted(self):
        row=self.row(next(iter(VERIFIED_MISSING_PROCESSING_FEE)))
        row["Processing Fee"]="0.00"
        self.assertFalse(detail_complete(row))
        row["Processing Fee"]="295.00"
        self.assertTrue(detail_complete(row))
if __name__=="__main__":
    unittest.main()
