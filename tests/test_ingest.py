"""Tests for the V1 load.

These encode the assumptions the text-to-SQL layer is allowed to rely on. If one
fails, the assumption is broken — fix the load or the assumption, never the test.
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from fda_agent.config import DB_PATH, V1_PATHWAY
from fda_agent.db import connect
from fda_agent.ingest import _recover_regulation_number, transform, validate

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
