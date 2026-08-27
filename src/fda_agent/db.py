"""Read-only access to the V1 database.

Every query path in this project goes through `connect()`. It opens DuckDB in
read-only mode, which is the enforcement mechanism behind the SQL invariant in
CLAUDE.md — a write cannot succeed even if one is somehow generated.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb

from fda_agent.config import DB_PATH


@contextmanager
def connect(db_path: Path = DB_PATH):
    """Yield a read-only DuckDB connection. Never opens a writable handle."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"{db_path} not found — run `python -m fda_agent.ingest` first"
        )
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        yield con
    finally:
        con.close()
