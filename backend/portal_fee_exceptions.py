"""Portal omissions manually verified by the dashboard owner on 2026-10-01.
Keep strict validation for all other tenders. Fresh positive portal fees win.
"""
VERIFIED_MISSING_PROCESSING_FEE = frozenset({
    "2026_DEON_535871_1", "2026_SOSEB_537381_1", "2026_SOSEB_537383_1",
    "2026_SETC_536588_1", "2026_SETC_536604_1", "2026_SOSEB_539052_1",
})
NOT_PROVIDED = "Not provided on portal"

def verified_fee_omission(row):
    return (str(row.get("Tender ID", "")).strip() in VERIFIED_MISSING_PROCESSING_FEE
            and row.get("Processing Fee") == NOT_PROVIDED)
