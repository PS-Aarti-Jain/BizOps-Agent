import os
import sqlite3
from typing import Optional
from app.config import DB_PATH


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target_path = db_path or os.getenv("DB_PATH") or DB_PATH
    conn = sqlite3.connect(str(target_path), timeout=10.0)
    conn.row_factory = dict_factory

    # Enable WAL mode, foreign keys, and 5000ms busy timeout
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn

