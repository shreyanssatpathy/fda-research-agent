"""Tests for the orchestration layer behind the UI.

The distinction under test is the one the interface depends on: a refusal, an
empty result, and a blocked query must be three different outcomes. If they
collapse, the UI shows "no clearances in 2026" for data that has no 2026.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fda_agent.answer import OUTCOMES, answer
from fda_agent.config import DB_PATH
from fda_agent.llm.budget import Budget
from fda_agent.text_to_sql import SqlDecision

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="database not built; run python -m fda_agent.ingest"
)


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Replace generation so these tests never hit the API."""

    def _install(decision: SqlDecision):
        gen = SimpleNamespace(
            decision=decision, model_id="m", prompt_version="p",
            schema_version="s", contract_hash="h", cached=True, cost_usd=0.0,
        )
        monkeypatch.setattr("fda_agent.answer.generate", lambda q, **kw: gen)

    return _install


def test_refusal_is_not_an_empty_result(patched):
    patched(SqlDecision(action="refuse", sql=None, explanation="Data ends in 2025."))
    a = answer("How many in 2026?")
    assert a.outcome == "refused"
    assert a.rows is None, "a refusal must not carry a dataframe"
    assert a.is_declined


def test_clarify_is_distinct_from_refuse(patched):
    patched(SqlDecision(action="clarify", sql=None, explanation="Which Samsung?"))
    a = answer("Show Samsung's clearances")
    assert a.outcome == "clarify"
    assert a.is_declined


def test_zero_rows_is_answered_as_empty_not_refused(patched):
    """A query that legitimately matches nothing is a result, not a refusal."""
    patched(SqlDecision(
        action="sql",
        sql="SELECT regnumber FROM fda_510k WHERE company_name = 'No Such Company'",
        explanation="",
    ))
    a = answer("clearances for a company that does not exist")
    assert a.outcome == "empty"
    assert not a.is_declined
    assert a.rows is not None and a.rows.empty


def test_successful_query_reports_rows_and_sql(patched):
    patched(SqlDecision(
        action="sql", sql="SELECT count(*) AS n FROM fda_510k", explanation="ok",
    ))
    a = answer("how many?")
    assert a.outcome == "answered"
    assert len(a.rows) == 1
    assert a.executed_sql and "LIMIT" in a.executed_sql
    assert a.duration_ms is not None


def test_unsafe_generated_sql_is_reported_as_blocked(patched):
    """A safety event must be visible, not rendered as a generic error."""
    patched(SqlDecision(
        action="sql", sql="SELECT * FROM read_csv_auto('/etc/passwd')", explanation="",
    ))
    a = answer("read a file")
    assert a.outcome == "blocked"
    assert "safety layer" in a.message


def test_blank_question_is_rejected_without_calling_the_model():
    a = answer("   ")
    assert a.outcome == "error"
    assert a.generation is None


def test_every_outcome_is_declared():
    for o in ("answered", "empty", "refused", "clarify", "blocked", "error"):
        assert o in OUTCOMES


# --- display helpers ----------------------------------------------------------------


def test_date_columns_render_without_midnight():
    import pandas as pd

    from fda_agent.display import for_display

    df = pd.DataFrame({"decision_date": pd.to_datetime(["2018-04-20", "2020-01-02"])})
    out = for_display(df)
    assert str(out["decision_date"].iloc[0]) == "2018-04-20"


def test_real_timestamps_are_left_alone():
    """Only strip a time component that is uniformly midnight."""
    import pandas as pd

    from fda_agent.display import for_display

    df = pd.DataFrame(
        {"t": pd.to_datetime(["2018-04-20 09:30:00", "2020-01-02 00:00:00"])}
    )
    out = for_display(df)
    assert pd.api.types.is_datetime64_any_dtype(out["t"])


def test_non_date_columns_untouched():
    import pandas as pd

    from fda_agent.display import for_display

    df = pd.DataFrame({"n": [1, 2], "name": ["a", "b"]})
    assert for_display(df).equals(df)
