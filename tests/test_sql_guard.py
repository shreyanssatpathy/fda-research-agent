"""Adversarial tests for the SQL guard.

The cases that matter are the filesystem readers. A read-only DuckDB connection
does not block them — verified, see test_read_only_alone_does_not_block_file_reads
— so the guard is the only thing standing between generated SQL and /etc/passwd.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from fda_agent.config import DB_PATH, MAX_ROWS
from fda_agent.db import connect
from fda_agent.sql_guard import SqlValidationError, validate

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="database not built; run python -m fda_agent.ingest"
)

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden_v1.yaml"


# --- the guard must not obstruct legitimate work --------------------------------


def test_every_golden_query_passes_the_guard():
    """If the guard rejects a reference query, the guard is wrong, not the query."""
    doc = yaml.safe_load(GOLDEN.read_text())
    checked = 0
    for case in doc["cases"]:
        sql = case.get("reference_sql")
        if not sql:
            continue
        try:
            validate(sql)
        except SqlValidationError as err:  # pragma: no cover - failure path
            pytest.fail(f"{case['id']} rejected: {err}")
        checked += 1
    assert checked == 27


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM fda_510k",
        "SELECT * FROM fda_510k WHERE company_name = 'Aidoc Medical'",
        "WITH c AS (SELECT company_name FROM fda_510k) SELECT count(*) FROM c",
        "SELECT a.company_name FROM fda_510k a JOIN fda_510k b USING (product_code)",
        "SELECT company_name FROM fda_510k UNION SELECT applicant_raw FROM fda_510k",
    ],
)
def test_valid_queries_accepted(sql):
    assert validate(sql).sql


# --- filesystem access: the real threat -----------------------------------------


def test_read_only_alone_does_not_block_file_reads():
    """Documents *why* the allowlist exists.

    This asserts the vulnerability is real: a plain SELECT reads a local file
    through a read-only connection. If DuckDB ever closes this, the test fails and
    the guard's rationale should be revisited — not silently kept.
    """
    with connect() as con:
        rows = con.execute("SELECT * FROM read_csv_auto('/etc/hosts') LIMIT 1").fetchall()
    assert rows, "expected read_csv_auto to succeed on a read-only connection"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT content FROM read_text('/etc/passwd')",
        "SELECT * FROM read_parquet('/tmp/x.parquet')",
        "SELECT * FROM read_json_auto('/tmp/x.json')",
        "SELECT * FROM glob('/**')",
        "SELECT * FROM fda_510k JOIN read_text('/etc/passwd') ON true",
        "SELECT (SELECT content FROM read_text('/etc/passwd')) FROM fda_510k",
        "WITH leak AS (SELECT * FROM read_csv_auto('/etc/passwd')) SELECT * FROM leak",
        "SELECT read_text('/etc/passwd') FROM fda_510k",
    ],
)
def test_filesystem_access_blocked(sql):
    with pytest.raises(SqlValidationError):
        validate(sql)


# --- write and control statements ------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE fda_510k",
        "DELETE FROM fda_510k",
        "UPDATE fda_510k SET company_name = 'x'",
        "INSERT INTO fda_510k VALUES (1)",
        "CREATE TABLE evil (x INT)",
        "ATTACH '/tmp/other.db' AS other",
        "COPY fda_510k TO '/tmp/leak.csv'",
        "PRAGMA database_list",
    ],
)
def test_non_select_rejected(sql):
    with pytest.raises(SqlValidationError):
        validate(sql)


def test_statement_chaining_rejected():
    with pytest.raises(SqlValidationError, match="single statement"):
        validate("SELECT 1 FROM fda_510k; DROP TABLE fda_510k")


def test_comment_hidden_second_statement_rejected():
    """A regex over the text could be fooled here; the parser is not."""
    with pytest.raises(SqlValidationError):
        validate("SELECT * FROM fda_510k /* harmless */ ; DELETE FROM fda_510k")


# --- allowlist --------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM ingest_metadata",
    ],
)
def test_non_allowlisted_tables_rejected(sql):
    """ingest_metadata exists in the database but is deliberately not queryable."""
    with pytest.raises(SqlValidationError):
        validate(sql)


def test_cte_names_are_not_mistaken_for_tables():
    v = validate("WITH fda_510k_x AS (SELECT 1 AS a FROM fda_510k) SELECT a FROM fda_510k_x")
    assert v.tables == frozenset({"fda_510k"})


# --- row limit ---------------------------------------------------------------------


def test_missing_limit_is_added():
    v = validate("SELECT * FROM fda_510k")
    assert v.limit_applied
    assert f"LIMIT {MAX_ROWS}" in v.sql


def test_tighter_limit_is_respected():
    v = validate("SELECT * FROM fda_510k LIMIT 5")
    assert not v.limit_applied
    assert "LIMIT 5" in v.sql


def test_oversized_limit_is_clamped():
    v = validate("SELECT * FROM fda_510k LIMIT 999999")
    assert v.limit_applied
    assert f"LIMIT {MAX_ROWS}" in v.sql
    assert "999999" not in v.sql


def test_limit_is_enforced_on_execution():
    from fda_agent.query import run

    r = run("SELECT * FROM fda_510k", question="everything", max_rows=7)
    assert len(r.rows) == 7


# --- malformed input ----------------------------------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", "SELECT FROM WHERE", "not sql at all"])
def test_malformed_rejected(sql):
    with pytest.raises(SqlValidationError):
        validate(sql)


def test_query_with_no_table_rejected():
    with pytest.raises(SqlValidationError, match="no allowlisted table"):
        validate("SELECT 1")
