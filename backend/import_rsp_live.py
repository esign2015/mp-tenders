"""Legacy RSP full-data import is disabled by the project source policy.

Only finalize_portal_snapshot/reconcile_run_snapshot may recover Tender IDs
from RSP, after three MP portal attempts and an unresolved count mismatch.
No date, title, reference, fee, PIN or other RSP field enters the master CSV.
"""

def main():
    print("RSP full-data import disabled. Use MP portal extraction; RSP is ID-only final mismatch fallback.")

if __name__ == "__main__":
    main()
