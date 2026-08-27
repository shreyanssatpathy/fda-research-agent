"""Tests for the V1 load.

These encode the assumptions the text-to-SQL layer is allowed to rely on. If one
fails, the assumption is broken — fix the load or the assumption, never the test.
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from fda_agent.config import DB_PATH, KNOWN_UNRESOLVED_APPLICANTS, V1_PATHWAY
from fda_agent.db import connect
from fda_agent.ingest import (
    _recover_regulation_number,
    resolve_company_names,
    transform,
    validate,
)

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="database not built; run python -m fda_agent.ingest"
)


@pytest.fixture(scope="module")
def rows() -> pd.DataFrame:
    with connect() as con:
        return con.execute("SELECT * FROM fda_510k").df()


def test_one_row_per_clearance(rows):
    """The grain of the table. Every count in the system depends on this."""
    assert rows["regnumber"].is_unique
    assert len(rows) == 1367


def test_only_510k(rows):
    assert set(rows["pathway"].unique()) == {V1_PATHWAY}


def test_no_pma_supplement_columns(rows):
    """Supplement columns fan a single PMA into many rows and would double-count."""
    for col in ("supplement_number", "supplementtype", "SUPPLEMENT NUMBER"):
        assert col not in rows.columns


def test_device_name_columns_stay_distinct(rows):
    """DEVICE NAME and DEVICENAME differ by one space and mean different things."""
    assert "device_trade_name" in rows.columns
    assert "device_classification_name" in rows.columns
    assert rows["device_trade_name"].nunique() > rows["device_classification_name"].nunique()


def test_regulation_number_recovered_as_string(rows):
    reg = rows["regulation_number"].dropna()
    assert reg.str.fullmatch(r"\d{3}\.\d{4}").all()
    assert "892.2050" in set(reg)


def test_recover_regulation_number_pads_lost_zero():
    assert _recover_regulation_number(892.205) == "892.2050"
    assert _recover_regulation_number(870.1025) == "870.1025"
    assert _recover_regulation_number(float("nan")) is None


def test_dates_are_dates(rows):
    assert pd.api.types.is_datetime64_any_dtype(
        pd.to_datetime(rows["decision_date"], errors="raise")
    )
    assert (rows["decision_date"] <= rows["date_received"].max()).any()


def test_coverage_boundary_is_end_of_2025(rows):
    """No 510(k) rows exist in 2026. Questions about 2026 are out of range, not zero.

    If this ever fails because new data arrived, update CLAUDE.md's V1 scope note
    in the same change.
    """
    assert rows["decision_year"].max() == 2025
    assert rows["decision_year"].min() == 2010


def test_no_unsuccessful_decisions(rows):
    """Only substantially-equivalent outcomes are present, so clearance-rate
    questions have no denominator and must be refused rather than computed."""
    assert set(rows["decision_code"].unique()) <= {"SESE", "SESU"}


def test_required_fields_never_null(rows):
    for col in ("regnumber", "company_name", "applicant_raw", "decision_date", "product_code"):
        assert rows[col].notna().all(), col


def test_connection_is_read_only():
    with connect() as con:
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE should_not_exist (x INTEGER)")


def test_validate_rejects_duplicate_regnumber():
    df = pd.DataFrame(
        {
            "regnumber": ["K1", "K1"],
            "pathway": [V1_PATHWAY] * 2,
            "company_name": ["A", "A"],
            "applicant_raw": ["A", "A"],
            "decision_date": pd.to_datetime(["2020-01-01", "2020-01-02"]).date,
            "product_code": ["ABC", "ABC"],
            "regulation_number": [None, None],
        }
    )
    with pytest.raises(ValueError, match="unique"):
        validate(df)


def test_transform_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing expected columns"):
        transform(pd.DataFrame({"PATHWAY": [V1_PATHWAY]}))


# --- company-name precedence (owner ruling 2026-08-27) --------------------------


def test_company_name_is_a_function_of_applicant(rows):
    """One applicant string must yield one company name.

    Only the three applicants where the authoritative pass contradicts itself are
    permitted to remain split; anything else is a regression.
    """
    counts = rows.groupby("applicant_raw")["company_name"].nunique()
    split = set(counts[counts > 1].index)
    assert split == set(KNOWN_UNRESOLVED_APPLICANTS)


def test_precedence_picks_the_authoritative_name(rows):
    """Spot-check cases the rule settled, across each losing source."""
    expected = {
        "RAYSEARCH LABORATORIES AB (PUBL)": "Raysearch Laboratories",  # beat pre-existing
        "VIZ. AI, INC.": "Viz.Ai",                                    # beat pre-existing
        "BODYVISION MEDICAL , LTD.": "Body Vision Medical",           # beat cleaning_script
        "SCOPIO LABS , LTD.": "Scopio",                               # beat AI_suggested
        "SPECTRUM DYNAMICS MEDICAL, LTD.": "Spectrum Dynamics Medical",  # beat manual_review
    }
    for applicant, name in expected.items():
        got = set(rows.loc[rows["applicant_raw"] == applicant, "company_name"])
        assert got == {name}, f"{applicant}: {got}"


def test_resolver_rejects_unknown_source():
    df = pd.DataFrame(
        {
            "applicant_raw": ["A CO", "A CO"],
            "company_name": ["A", "B"],
            "company_name_source": ["ming-mapping", "some-new-pass"],
        }
    )
    with pytest.raises(ValueError, match="SOURCE_PRECEDENCE"):
        resolve_company_names(df)


def test_resolver_leaves_self_contradicting_source_alone():
    """When the top-precedence pass disagrees with itself there is no defensible
    winner, so the rows stay split and are reported rather than guessed at."""
    df = pd.DataFrame(
        {
            "applicant_raw": ["A CO", "A CO"],
            "company_name": ["A", "B"],
            "company_name_source": ["ming-mapping", "ming-mapping"],
        }
    )
    out, unresolved = resolve_company_names(df)
    assert unresolved == ["A CO"]
    assert set(out["company_name"]) == {"A", "B"}
