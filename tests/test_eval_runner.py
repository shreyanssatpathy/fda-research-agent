"""Self-consistency checks on the eval harness.

If the scorer cannot recognise the reference SQL as correct, every score it
produces is meaningless. These run offline — no API key, no generation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from fda_agent.config import DB_PATH  # noqa: E402
from fda_agent.query import run as run_sql  # noqa: E402
from run import SAMPLE_IDS, score_answer  # noqa: E402

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="database not built; run python -m fda_agent.ingest"
)

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden_v1.yaml"
CASES = [c for c in yaml.safe_load(GOLDEN.read_text())["cases"] if c.get("reference_sql")]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_sql_scores_as_a_pass(case):
    """The golden answer must score correct against itself.

    A failure here means the scorer is broken, or the frozen expected answer no
    longer matches the data — not that a model got something wrong.
    """
    result = run_sql(case["reference_sql"], question=case["question"])
    ok, detail = score_answer(case, result.rows)
    assert ok, f"{case['id']}: {detail}"


def test_scorer_rejects_a_wrong_row_count():
    case = next(c for c in CASES if c["expected_answer"]["row_count"] == 1)
    result = run_sql(
        "SELECT company_name FROM fda_510k LIMIT 3", question="deliberately wrong"
    )
    ok, detail = score_answer(case, result.rows)
    assert not ok
    assert "row count" in detail


def test_scorer_rejects_right_shape_wrong_values():
    """Catches the dangerous near-miss: plausible SQL, wrong answer."""
    case = next(c for c in CASES if c["id"] == "C01")  # total clearances = 1367
    wrong = run_sql(
        "SELECT count(*) AS n FROM fda_510k WHERE decision_year = 2023",
        question="deliberately wrong",
    )
    ok, detail = score_answer(case, wrong.rows)
    assert not ok
    assert "values differ" in detail


def test_scorer_ignores_column_naming():
    """`n` vs `total` is style, not error."""
    case = next(c for c in CASES if c["id"] == "C01")
    renamed = run_sql("SELECT count(*) AS total FROM fda_510k", question="alias")
    ok, _ = score_answer(case, renamed.rows)
    assert ok


def test_sample_ids_all_exist_in_the_frozen_set():
    all_ids = {c["id"] for c in yaml.safe_load(GOLDEN.read_text())["cases"]}
    assert set(SAMPLE_IDS) <= all_ids


def test_sample_covers_answerable_and_unanswerable():
    """A sample of only answerable cases would hide the refusal failures."""
    by_id = {c["id"]: c for c in yaml.safe_load(GOLDEN.read_text())["cases"]}
    kinds = {by_id[i]["expects"] for i in SAMPLE_IDS}
    assert kinds == {"answer", "refusal_or_clarification"}
