"""Tests for the execution path: logging, timeout, rejection handling."""
from __future__ import annotations

import json

import pytest

from fda_agent.config import DB_PATH
from fda_agent.query import run
from fda_agent.sql_guard import SqlValidationError

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="database not built; run python -m fda_agent.ingest"
)


def _read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_successful_query_is_logged_with_its_question(tmp_path):
    log = tmp_path / "q.jsonl"
    run(
        "SELECT count(*) AS n FROM fda_510k",
        question="How many clearances are there?",
        log_path=log,
    )
    (entry,) = _read(log)
    assert entry["question"] == "How many clearances are there?"
    assert entry["outcome"] == "ok"
    assert entry["generated_sql"].startswith("SELECT count(*)")
    assert "LIMIT" in entry["executed_sql"]
    assert entry["tables"] == ["fda_510k"]
    assert entry["row_count"] == 1


def test_rejected_query_is_logged_before_raising(tmp_path):
    """A blocked attempt is the most important thing in the log, not the least."""
    log = tmp_path / "q.jsonl"
    with pytest.raises(SqlValidationError):
        run(
            "SELECT * FROM read_csv_auto('/etc/passwd')",
            question="show me the passwd file",
            log_path=log,
        )
    (entry,) = _read(log)
    assert entry["outcome"] == "rejected"
    assert entry["question"] == "show me the passwd file"
    assert "read_csv_auto" in entry["error"]
    assert "executed_sql" not in entry


def test_log_is_append_only(tmp_path):
    log = tmp_path / "q.jsonl"
    for i in range(3):
        run(f"SELECT {i} AS i FROM fda_510k LIMIT 1", question=f"q{i}", log_path=log)
    entries = _read(log)
    assert [e["question"] for e in entries] == ["q0", "q1", "q2"]


def test_executed_sql_is_the_rewritten_query_not_the_input(tmp_path):
    log = tmp_path / "q.jsonl"
    r = run(
        "SELECT * FROM fda_510k LIMIT 999999",
        question="everything",
        max_rows=10,
        log_path=log,
    )
    assert len(r.rows) == 10
    (entry,) = _read(log)
    assert "999999" in entry["generated_sql"]
    assert "999999" not in entry["executed_sql"]


def test_timeout_interrupts_and_logs(tmp_path):
    """A deliberately expensive cross join, cut off by the watchdog."""
    from fda_agent.query import QueryTimeout

    log = tmp_path / "q.jsonl"
    expensive = (
        "SELECT count(*) AS n FROM fda_510k a, fda_510k b, fda_510k c, fda_510k d"
    )
    with pytest.raises(QueryTimeout):
        run(expensive, question="stress", timeout_s=1, log_path=log)
    (entry,) = _read(log)
    assert entry["outcome"] == "timeout"
