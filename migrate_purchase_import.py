"""One-time migration: adds the "Import from Vendor File" feature on the
Purchases tab (upload an SO Details-style Excel export from a supplier,
match its material codes to your Products, and create the Purchase for
you instead of typing every line by hand).

This does NOT touch any existing data:
  - Creates one new table, SupplierProductMap (CREATE TABLE IF NOT
    EXISTS) - it remembers which of a supplier's material codes maps to
    which of your Products, so re-uploading a file from the same supplier
    auto-matches next time. Empty until you use the import feature.
  - Nothing on Purchases, PurchaseLines, or Products is changed - the
    import feature reuses the GST columns already added by
    migrate_gst_filing.py (run that one first if you haven't).

Safe to run multiple times.

Usage: python migrate_purchase_import.py
"""
import db


def run():
    db.init_db()  # creates SupplierProductMap if missing (CREATE TABLE IF NOT EXISTS)
    print("Migration complete.")
    print("\nWhat's new: on the Purchases page, an 'Import from Vendor File' button lets you")
    print("upload a supplier's SO Details Excel export. You'll match each material code to one")
    print("of your Products (or create a new one) the first time - the app remembers that match")
    print("for next time you import a file from the same supplier.")


if __name__ == "__main__":
    run()
