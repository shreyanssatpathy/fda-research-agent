"""The golden set is frozen. These tests exist to make that real.

Per CLAUDE.md the set is never regenerated or edited to improve a score. The hash
guard turns that from a convention into a failing build.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals"
GOLDEN = EVAL_DIR / "golden_v1.yaml"
DIGEST = EVAL_DIR / "golden_v1.sha256"


def test_golden_set_is_unmodified():
    """If this fails, the frozen set was edited. Do not update the hash to make it
    pass — establish first whether the change was intended and record it in
    evals/README.md under Amendments."""
    expected = DIGEST.read_text().split()[0]
    actual = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    assert actual == expected, "frozen golden set has been modified"


def test_golden_set_is_marked_frozen():
    doc = yaml.safe_load(GOLDEN.read_text())
    assert doc["frozen"] is True
    assert doc["frozen_on"] == "2026-08-27"


def test_case_ids_unique_and_categories_populated():
    doc = yaml.safe_load(GOLDEN.read_text())
    ids = [c["id"] for c in doc["cases"]]
    assert len(ids) == len(set(ids)) == 38
    assert all(c["category"] for c in doc["cases"])


def test_refusal_cases_carry_no_reference_sql():
    """A refusal case with SQL attached would be scored as answerable by mistake."""
    doc = yaml.safe_load(GOLDEN.read_text())
    for case in doc["cases"]:
        if case["expects"] == "refusal_or_clarification":
            assert case.get("reference_sql") is None, case["id"]
            assert case.get("note"), f"{case['id']} must say why it is refused"


def test_refusal_coverage_is_material():
    """A system that answers every question has failed. Guard the ratio."""
    doc = yaml.safe_load(GOLDEN.read_text())
    refusals = [c for c in doc["cases"] if c["expects"] == "refusal_or_clarification"]
    assert len(refusals) == 11  # 8 refusals + 3 clarifications, of 38
