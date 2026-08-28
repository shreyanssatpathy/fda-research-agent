"""Tests for the PitchBook load.

The rules under test are the owner's cleaning rules (2026-08-27) plus the grain
assumptions everything downstream depends on. The most important is deal_id
uniqueness: if the deal table ever fans out, every SUM(deal_size) silently
multiplies.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fda_agent.config import DB_PATH
from fda_agent.db import connect
from fda_agent.ingest_pitchbook import (
    QUALIFIED_FINANCING_STATUS,
    clean_companies,
    is_qualified_universe,
    validate,
)


def _has(table: str) -> bool:
    if not DB_PATH.exists():
        return False
    with connect() as con:
        return bool(
            con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                [table],
            ).fetchone()[0]
        )


pytestmark = pytest.mark.skipif(
    not _has("pb_deals"),
    reason="PitchBook tables not built; run python -m fda_agent.ingest_pitchbook",
)


@pytest.fixture(scope="module")
def deals():
    with connect() as con:
        return con.execute("SELECT * FROM pb_deals").df()


@pytest.fixture(scope="module")
def companies():
    with connect() as con:
        return con.execute("SELECT * FROM pb_companies").df()


@pytest.fixture(scope="module")
def bridge():
    with connect() as con:
        return con.execute("SELECT * FROM fda_pb_bridge").df()


# --- grain: the assumption every aggregate rests on --------------------------------


def test_one_row_per_deal(deals):
    """If this fails, SUM(deal_size_usd_m) multiplies by the fan-out factor."""
    assert deals["deal_id"].is_unique


def test_one_row_per_company(companies):
    """The export is denormalised to FDA clearance grain — 1,483 rows for 475
    companies — so without deduplication every company aggregate is weighted by
    how many devices that company cleared."""
    assert companies["company_id"].is_unique
    assert len(companies) == 475


def test_bridge_is_a_function(bridge):
    """One FDA submission resolves to exactly one PitchBook company."""
    assert bridge.groupby("regnumber")["company_id"].nunique().max() == 1


# --- the four cleaning rules --------------------------------------------------------


def test_rule1_no_missing_deal_size(deals):
    assert deals["deal_size_usd_m"].notna().all()


def test_rule2_completed_only(deals):
    assert set(deals["deal_status"].unique()) == {"Completed"}


def test_rule3_qualified_universe_only(deals):
    assert deals["universe"].map(is_qualified_universe).all()


def test_rule4_qualified_financing_status_only(deals, companies):
    qualified = set(
        companies.loc[
            companies["financing_status"].isin(QUALIFIED_FINANCING_STATUS), "company_id"
        ]
    )
    assert set(deals["company_id"]) <= qualified


def test_universe_qualifier_rejects_nulls_and_non_matches():
    """Absence of a universe label is not membership."""
    assert is_qualified_universe("Debt Financed, Venture Capital")
    assert is_qualified_universe("Pre-venture")
    assert not is_qualified_universe("M&A, Private Equity")
    assert not is_qualified_universe(None)
    assert not is_qualified_universe(float("nan"))


# --- companies keep everyone, flagged ------------------------------------------------


def test_companies_table_keeps_non_venture_firms(companies):
    """Rule 4 filters deals, not companies.

    Dropping GE Healthcare from the company table would make "no funding data"
    indistinguishable from "public corporation, outside the venture universe".
    """
    assert (~companies["in_qualified_universe"]).sum() > 0
    statuses = set(companies.loc[~companies["in_qualified_universe"], "financing_status"].dropna())
    assert "Corporation" in statuses


def test_qualified_flag_matches_the_status_list(companies):
    expected = companies["financing_status"].isin(QUALIFIED_FINANCING_STATUS)
    assert (companies["in_qualified_universe"] == expected).all()


# --- validation refuses to publish a bad table ---------------------------------------


def test_validate_rejects_duplicate_deal_ids():
    deals = pd.DataFrame({
        "deal_id": ["D1", "D1"],
        "company_id": ["C1", "C1"],
        "deal_size_usd_m": [1.0, 2.0],
        "deal_status": ["Completed", "Completed"],
        "universe": ["Venture Capital", "Venture Capital"],
    })
    comps = pd.DataFrame({"company_id": ["C1"]})
    bridge = pd.DataFrame({"regnumber": ["K1"], "company_id": ["C1"]})
    with pytest.raises(ValueError, match="deal_id must be unique"):
        validate(deals, comps, bridge)


def test_validate_rejects_a_non_function_bridge():
    deals = pd.DataFrame({
        "deal_id": ["D1"], "company_id": ["C1"], "deal_size_usd_m": [1.0],
        "deal_status": ["Completed"], "universe": ["Venture Capital"],
    })
    comps = pd.DataFrame({"company_id": ["C1"]})
    bridge = pd.DataFrame({"regnumber": ["K1", "K1"], "company_id": ["C1", "C2"]})
    with pytest.raises(ValueError, match="not a function"):
        validate(deals, comps, bridge)


def test_validate_rejects_orphan_deals():
    deals = pd.DataFrame({
        "deal_id": ["D1"], "company_id": ["GHOST"], "deal_size_usd_m": [1.0],
        "deal_status": ["Completed"], "universe": ["Venture Capital"],
    })
    comps = pd.DataFrame({"company_id": ["C1"]})
    bridge = pd.DataFrame({"regnumber": ["K1"], "company_id": ["C1"]})
    with pytest.raises(ValueError, match="unknown companies"):
        validate(deals, comps, bridge)


def test_clean_companies_deduplicates_the_denormalised_export():
    raw = pd.DataFrame({
        "Company ID": ["C1", "C1", "C2"],
        "Companies": ["A", "A", "B"],
        "Company Legal Name": ["A Inc", "A Inc", "B Inc"],
        "Company Former Name": [None, None, None],
        "Company Financing Status": ["Venture Capital-Backed"] * 2 + ["Corporation"],
        "Business Status": ["Generating Revenue"] * 3,
        "Ownership Status": ["Privately Held"] * 3,
        "Year Founded": [2015, 2015, 2001],
        "Total Raised": [10.0, 10.0, None],
        "HQ Country/Territory/Region": ["United States"] * 3,
        "HQ City": ["Boston"] * 3,
        "Website": ["a.com", "a.com", "b.com"],
        "Universe": ["Venture Capital"] * 3,
    })
    out = clean_companies(raw)
    assert len(out) == 2
    assert out.set_index("company_id").loc["C1", "in_qualified_universe"]
    assert not out.set_index("company_id").loc["C2", "in_qualified_universe"]
