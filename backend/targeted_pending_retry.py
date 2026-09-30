"""Verify incomplete and historical imported live rows on the official MP portal."""
import os

# Inventory includes copied live rows missing from a partial organisation file.
os.environ.setdefault("EXTRACT_ALL_INVENTORY", "1")
os.environ.setdefault("PRIMARY_ID_SEARCH", "1")
os.environ.setdefault("DETAIL_SEARCH_WORKERS", "3")
os.environ.setdefault("RSP_FAST", "0")
os.environ.setdefault("REPAIR_COMPLETED_DETAILS", "0")
os.environ.setdefault("EXISTING_ID_BATCH_SIZE", "500")

from existing_id_detail import main

if __name__ == "__main__":
    main()
