"""SQLite connection helper for the Rental360 warehouse.

All connections are read-only (``mode=ro`` URI) so the API and the
deterministic assistant can never mutate the warehouse. The database path
comes from the ``RENTAL360_DB`` environment variable, or defaults to
``data/warehouse/rental360.db`` resolved relative to the repository root
(the parent of this file's ``app/`` package).

ALL DATA IN THIS WAREHOUSE IS SYNTHETIC — generated for the Rental360
demo project. Never present it as real business data.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB_PATH = REPO_ROOT / "data" / "warehouse" / "rental360.db"


def get_db_path() -> Path:
    """Resolve the warehouse path from env or the default location."""
    raw = os.environ.get("RENTAL360_DB", "").strip()
    if not raw:
        return DEFAULT_DB_PATH
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


class DatabaseUnavailableError(RuntimeError):
    """Raised when the warehouse file is missing or unreadable."""


def get_connection() -> sqlite3.Connection:
    """Open a read-only connection to the warehouse.

    Raises:
        DatabaseUnavailableError: if the database file does not exist.
    """
    path = get_db_path()
    if not path.exists():
        raise DatabaseUnavailableError(
            f"Rental360 warehouse database not found at {path}. "
            "Set RENTAL360_DB to the warehouse path or run the ETL to build it."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    """Run a read-only SELECT and return rows as a list of dicts."""
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    """Run a read-only SELECT and return the first row as a dict, or None."""
    rows = fetch_all(sql, params)
    return rows[0] if rows else None
