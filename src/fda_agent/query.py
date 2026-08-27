"""The single execution path for generated SQL.

Nothing else in the project runs model-written SQL. This is where the five
required safeguards meet: SELECT-only, allowlist and row limit (via `sql_guard`),
read-only connection and statement timeout (here).

Every execution is logged with the question that produced it, per CLAUDE.md.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from fda_agent.config import DATA_DIR, DB_PATH, MAX_ROWS, STATEMENT_TIMEOUT_S
from fda_agent.db import connect
from fda_agent.sql_guard import SqlValidationError, ValidatedQuery, validate

QUERY_LOG = DATA_DIR / "logs" / "queries.jsonl"


class QueryTimeout(Exception):
    """Raised when a query exceeds STATEMENT_TIMEOUT_S and is interrupted."""


@dataclass(frozen=True)
class QueryResult:
    question: str
    validated: ValidatedQuery
    rows: pd.DataFrame
    duration_ms: int


def _log(record: dict, log_path: Path) -> None:
    """Append-only. One JSON object per line, never rewritten."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def run(
    sql: str,
    *,
    question: str,
    db_path: Path = DB_PATH,
    timeout_s: int = STATEMENT_TIMEOUT_S,
    max_rows: int = MAX_ROWS,
    log_path: Path = QUERY_LOG,
) -> QueryResult:
    """Validate, execute under a timeout, and log. Raises on rejection or timeout.

    The SQL executed is always the guard's rewritten output, never the input
    string — so the row limit cannot be bypassed by what the model wrote.
    """
    started = datetime.now(timezone.utc)
    record: dict = {
        "at": started.isoformat(timespec="milliseconds"),
        "question": question,
        "generated_sql": sql,
    }

    try:
        validated = validate(sql, max_rows=max_rows)
    except SqlValidationError as err:
        record.update(outcome="rejected", error=str(err))
        _log(record, log_path)
        raise

    record.update(
        executed_sql=validated.sql,
        tables=sorted(validated.tables),
        limit_applied=validated.limit_applied,
    )

    with connect(db_path) as con:
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            rows = con.execute(validated.sql).df()
        except duckdb.InterruptException as err:
            record.update(outcome="timeout", error=f"exceeded {timeout_s}s")
            _log(record, log_path)
            raise QueryTimeout(f"query exceeded {timeout_s}s and was interrupted") from err
        except duckdb.Error as err:
            record.update(outcome="error", error=str(err))
            _log(record, log_path)
            raise
        finally:
            timer.cancel()

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    record.update(outcome="ok", row_count=len(rows), duration_ms=duration_ms)
    _log(record, log_path)

    return QueryResult(
        question=question,
        validated=validated,
        rows=rows,
        duration_ms=duration_ms,
    )
