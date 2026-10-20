"""
V60z38 — Delete old Item Registration data (one-time cleanup).

Run this in PythonAnywhere Bash console:
    cd /home/serveone/mysite && python3 clear_item_reg.py

This will:
1. Delete ALL rows from item_registration table
2. Reset the auto-increment counter
3. Set the migration flag so app.py doesn't try to clear again
"""
import sqlite3
import os
import glob
from datetime import datetime

# V60z38: DB is in instance/ subfolder (per app.py line 224-226)
# Try multiple possible locations
POSSIBLE_PATHS = [
    '/home/serveone/mysite/instance/po_database.db',
    '/home/serveone/mysite/po_database.db',
    '/home/serveone/mysite/pythonanywhere.db',
    '/home/serveone/mysite/db.sqlite3',
]

# Also search for any .db file in mysite/ and mysite/instance/
for pattern in ['/home/serveone/mysite/*.db', '/home/serveone/mysite/instance/*.db', '/home/serveone/mysite/*.sqlite3', '/home/serveone/mysite/instance/*.sqlite3']:
    POSSIBLE_PATHS.extend(glob.glob(pattern))

# Deduplicate + filter existing
DB_PATHS = []
seen = set()
for p in POSSIBLE_PATHS:
    if p not in seen and os.path.exists(p):
        DB_PATHS.append(p)
        seen.add(p)

if not DB_PATHS:
    print("ERROR: Database file not found.")
    print()
    print("Searched locations:")
    for p in POSSIBLE_PATHS:
        print(f"  {p}")
    print()
    print("Files in /home/serveone/mysite/:")
    try:
        for f in sorted(os.listdir('/home/serveone/mysite/')):
            full = os.path.join('/home/serveone/mysite/', f)
            if os.path.isfile(full):
                size = os.path.getsize(full)
                if size > 100000 or f.endswith('.db') or f.endswith('.sqlite3'):
                    print(f"  {f} ({size:,} bytes)")
    except Exception as e:
        print(f"  (error listing: {e})")
    print()
    print("Files in /home/serveone/mysite/instance/:")
    try:
        for f in sorted(os.listdir('/home/serveone/mysite/instance/')):
            full = os.path.join('/home/serveone/mysite/instance/', f)
            if os.path.isfile(full):
                size = os.path.getsize(full)
                print(f"  {f} ({size:,} bytes)")
    except Exception as e:
        print(f"  (error listing: {e})")
    exit(1)

# If multiple DB files found, let user choose
if len(DB_PATHS) == 1:
    DB_PATH = DB_PATHS[0]
else:
    print("Multiple database files found:")
    for i, p in enumerate(DB_PATHS):
        size = os.path.getsize(p)
        print(f"  [{i+1}] {p} ({size:,} bytes)")
    choice = input(f"\nWhich database to use? (1-{len(DB_PATHS)}): ").strip()
    try:
        idx = int(choice) - 1
        DB_PATH = DB_PATHS[idx]
    except (ValueError, IndexError):
        print("Invalid choice.")
        exit(1)

print(f"Database: {DB_PATH}")
print(f"Size: {os.path.getsize(DB_PATH):,} bytes")
print()

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Check if item_registration table exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='item_registration'")
if not cur.fetchone():
    print("⚠️  Table 'item_registration' does not exist in this database.")
    print("    This might be the wrong database file.")
    exit(1)

# Check current count
cur.execute("SELECT COUNT(*) FROM item_registration")
count = cur.fetchone()[0]
print(f"Current item_registration rows: {count}")
print()

if count == 0:
    print("✅ Table is already empty. Nothing to delete.")
    # Still set the migration flag
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                    ('item_reg_v60z37_cleared', datetime.utcnow().isoformat()))
        conn.commit()
        print("✅ Set migration flag (app.py won't try to clear again)")
    except Exception as e:
        print(f"(migration flag skipped: {e})")
else:
    confirm = input(f"Delete {count} rows from item_registration? Type 'YES' to confirm: ")
    if confirm != 'YES':
        print("Cancelled.")
        exit(0)
    print()
    print(f"Deleting {count} rows...")
    cur.execute("DELETE FROM item_registration")
    conn.commit()
    print(f"✅ Deleted {count} rows")

    # Reset auto-increment (SQLite)
    try:
        cur.execute("DELETE FROM sqlite_sequence WHERE name='item_registration'")
        conn.commit()
        print("✅ Reset auto-increment counter")
    except Exception as e:
        print(f"(auto-increment reset skipped: {e})")

    # Set migration flag so app.py doesn't try to clear again
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                    ('item_reg_v60z37_cleared', datetime.utcnow().isoformat()))
        conn.commit()
        print("✅ Set migration flag (app.py won't clear again)")
    except Exception as e:
        print(f"(migration flag skipped: {e})")

# Verify
cur.execute("SELECT COUNT(*) FROM item_registration")
final_count = cur.fetchone()[0]
print()
print(f"Final item_registration rows: {final_count}")
print()
print("✅ DONE. Now:")
print("   1. Reload web app in PythonAnywhere Web tab")
print("   2. Upload the new Prod. Reg. Status Excel file via web app")

conn.close()
