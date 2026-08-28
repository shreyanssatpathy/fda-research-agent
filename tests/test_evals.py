"""The golden set is frozen. These tests exist to make that real.

Per CLAUDE.md the set is never regenerated or edited to improve a score. The hash
guard turns that from a convention into a failing build.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals"
GOLDEN = EVAL_DIR / "golden_v1.yaml"
DIGEST = EVAL_DIR / "golden_v1.sha256"

SETS = [
    ("golden_v1.yaml", "golden_v1.sha256"),
    ("golden_v2.yaml", "golden_v2.sha256"),
    ("golden_v3.yaml", "golden_v3.sha256"),
]


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


# --- v2 ---------------------------------------------------------------------------


@pytest.mark.parametrize("yaml_name,digest_name", SETS)
def test_every_frozen_set_is_unmodified(yaml_name, digest_name):
    expected = (EVAL_DIR / digest_name).read_text().split()[0]
    actual = hashlib.sha256((EVAL_DIR / yaml_name).read_bytes()).hexdigest()
    assert actual == expected, f"{yaml_name} has been modified"


def test_v1_is_never_altered_by_v2_work():
    """v2 supersedes v1; it must not edit it. The sets stay comparable."""
    v1 = yaml.safe_load((EVAL_DIR / "golden_v1.yaml").read_text())
    v2 = yaml.safe_load((EVAL_DIR / "golden_v2.yaml").read_text())
    assert v2["supersedes"] == "golden_v1.yaml"
    assert v1["schema_version"] == "1.0.0"
    assert {c["id"] for c in v1["cases"]} == {c["id"] for c in v2["cases"]}


def test_v2_corrections_each_cite_a_reason():
    """A changed reference without a recorded justification is indistinguishable
    from tuning the set to fit the model."""
    v2 = yaml.safe_load((EVAL_DIR / "golden_v2.yaml").read_text())
    changed = [c for c in v2["cases"] if c.get("changed_from_v1")]
    assert len(changed) == 9
    for case in changed:
        assert len(case["changed_from_v1"]) > 20, case["id"]


def test_v2_questions_are_identical_to_v1():
    """Only reference SQL and expectations changed. Rewording questions to suit
    the system would make the two sets incomparable."""
    v1 = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden_v1.yaml").read_text())["cases"]}
    v2 = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden_v2.yaml").read_text())["cases"]}
    for cid, case in v2.items():
        assert case["question"] == v1[cid]["question"], cid


def test_v2_still_has_material_decline_coverage():
    v2 = yaml.safe_load((EVAL_DIR / "golden_v2.yaml").read_text())
    declines = [c for c in v2["cases"] if c["expects"] in ("refuse", "clarify", "decline")]
    assert len(declines) == 12


# --- v3 ---------------------------------------------------------------------------


def test_v3_questions_still_identical_to_v1():
    """Three sets deep, the questions must still be the originals. Rewording to
    suit the system at any point would break the whole comparison chain."""
    v1 = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden_v1.yaml").read_text())["cases"]}
    v3 = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden_v3.yaml").read_text())["cases"]}
    assert set(v1) == set(v3)
    for cid, case in v3.items():
        assert case["question"] == v1[cid]["question"], cid


def test_v3_corrections_cite_a_reason():
    v3 = yaml.safe_load((EVAL_DIR / "golden_v3.yaml").read_text())
    changed = [c for c in v3["cases"] if c.get("changed_in_v3")]
    assert {c["id"] for c in changed} == {"M01", "D05", "G01"}
    for case in changed:
        assert len(case["changed_in_v3"]) > 20, case["id"]


def test_v3_listings_carry_every_column():
    """Rule 14: a clearance listing returns the whole record."""
    v3 = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden_v3.yaml").read_text())["cases"]}
    for cid in ("M01", "D05"):
        assert len(v3[cid]["expected_answer"]["columns"]) == 24, cid


def test_v3_preserves_v2_corrections():
    """v3 builds on v2; the nine earlier corrections must survive."""
    v3 = yaml.safe_load((EVAL_DIR / "golden_v3.yaml").read_text())
    assert len([c for c in v3["cases"] if c.get("changed_in_v2")]) == 9
