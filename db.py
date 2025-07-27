# db.py
import sqlite3, json
from pathlib import Path
from datetime import datetime

DB_PATH = Path("invoice_data.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  invoice_no    TEXT UNIQUE,
  payload_json  TEXT
);
"""

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    with conn:
        conn.executescript(SCHEMA)
    conn.close()

def next_invoice_no():
    """Insert a placeholder row to reserve an ID, then format with year."""
    conn = get_conn()
    with conn:
        cur = conn.execute("INSERT INTO invoices (invoice_no, payload_json) VALUES (?, ?)", (None, "{}"))
        new_id = cur.lastrowid
    conn.close()
    year = datetime.now().year
    return f"{year}-{new_id:05d}", new_id

def persist_invoice(invoice_no, payload):
    """Update the row (if placeholder) or insert fresh if user supplied a custom number."""
    conn = get_conn()
    with conn:
        # Try update placeholder
        updated = conn.execute(
            "UPDATE invoices SET invoice_no=?, payload_json=? WHERE invoice_no IS NULL",
            (invoice_no, json.dumps(payload))
        ).rowcount
        if updated == 0:
            # User supplied custom number first time
            conn.execute(
                "INSERT OR REPLACE INTO invoices (invoice_no, payload_json) VALUES (?, ?)",
                (invoice_no, json.dumps(payload))
            )
    conn.close()
