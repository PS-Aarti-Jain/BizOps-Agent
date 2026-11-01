from pathlib import Path
import sqlite3
import sys

# Relative path resolution without hardcoded absolute machine paths
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DB_PATH

if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Database file not found at: {DB_PATH}")
        sys.exit(0)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r["name"] for r in cursor.fetchall()]
    print("Tables:", tables)
    for t in tables:
        cursor.execute("SELECT * FROM " + t + " LIMIT 1")
        row = cursor.fetchone()
        if row:
            print(t + ": " + str(list(row.keys())))
        else:
            print(t + ": (empty)")
    conn.close()
