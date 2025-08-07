# db.py
import sqlite3, json
from sqlite3 import Row
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple


DB_PATH = Path("invoice_data.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  invoice_no    TEXT UNIQUE,
  payload_json  TEXT,
  pdf_path      TEXT
);
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = Row        
    return conn

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

def update_pdf_path(invoice_no: str, pdf_path: str):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE invoices SET pdf_path=? WHERE invoice_no=?",
            (pdf_path, invoice_no)
        )
    conn.close()

def list_invoices(search: Optional[str]) -> List[sqlite3.Row]:
    conn = get_conn()
    params = []
    where = ""
    if search:
        where = "WHERE invoice_no LIKE ? OR LOWER(json_extract(payload_json,'$.client')) LIKE ?"
        like = f"%{search}%"
        params += [like, like.lower()]
    rows = conn.execute(
        f"""SELECT invoice_no, created_at, payload_json, pdf_path
            FROM invoices
            {where}
            ORDER BY created_at DESC""",
        params
    ).fetchall()
    conn.close()
    return rows

def get_invoice(invoice_no):
    conn = get_conn()
    row = conn.execute(
        "SELECT invoice_no, created_at, payload_json, pdf_path FROM invoices WHERE invoice_no=?",
        (invoice_no,),
    ).fetchone()
    conn.close()
    return row

def delete_invoice(invoice_no):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM invoices WHERE invoice_no=?", (invoice_no,))
    conn.close()

