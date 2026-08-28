"""The contracts state facts about the data. These assert those facts are true.

A semantic contract that has drifted from the database is worse than none: the
model is told, authoritatively, something false. Every number quoted in a contract
should be checked here, so a reload that changes the data fails the build instead
of silently invalidating the prompt.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from fda_agent.config import DB_PATH
from fda_agent.db import connect

CONTRACTS = Path(__file__).resolve().parents[1] / "docs" / "contracts"


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
    not _has("pb_deals"), reason="PitchBook tables not built"
)


@pytest.fixture(scope="module")
def counts() -> dict:
    with connect() as con:
        q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
        return {
            "deals": q("SELECT count(*) FROM pb_deals"),
            "companies": q("SELECT count(*) FROM pb_companies"),
            "bridge": q("SELECT count(*) FROM fda_pb_bridge"),
            "venture": q("SELECT count(*) FROM pb_deals WHERE is_venture_round"),
            "non_venture": q("SELECT count(*) FROM pb_deals WHERE NOT is_venture_round"),
            "qualified": q("SELECT count(*) FROM pb_companies WHERE in_qualified_universe"),
            "unqualified": q("SELECT count(*) FROM pb_companies WHERE NOT in_qualified_universe"),
            "fda_companies": q("SELECT count(DISTINCT company_name) FROM fda_510k"),
            "with_deals": q(
                "SELECT count(DISTINCT f.company_name) FROM fda_510k f "
                "JOIN fda_pb_bridge b USING (regnumber) "
                "JOIN pb_deals d ON d.company_id = b.company_id"
            ),
            "unbridged": q("SELECT count(*) FROM fda_510k WHERE pb_company_id IS NULL"),
            "null_deal_date": q("SELECT count(*) FROM pb_deals WHERE deal_date IS NULL"),
        }


def test_pitchbook_contract_row_counts_are_current(counts):
    text = (CONTRACTS / "pitchbook.md").read_text()
    assert f"| **one row per deal** | {counts['deals']:,} |" in text
    assert f"| **one row per company** | {counts['companies']:,} |" in text
    assert f"| **one row per FDA submission** | {counts['bridge']:,} |" in text


def test_pitchbook_contract_venture_split_is_current(counts):
    text = (CONTRACTS / "pitchbook.md").read_text()
    assert f"| `is_venture_round = true` | {counts['venture']:,} |" in text
    assert f"| `is_venture_round = false` | {counts['non_venture']:,} |" in text


def test_pitchbook_contract_coverage_is_current(counts):
    text = (CONTRACTS / "pitchbook.md").read_text()
    assert f"{counts['with_deals']} of the {counts['fda_companies']} FDA companies" in text
    assert f"the other {counts['fda_companies'] - counts['with_deals']}" in text
    assert f"{counts['qualified']} true / {counts['unqualified']} false" in text
    assert f"**{counts['null_deal_date']} nulls**" in text


def test_fda_contract_company_count_is_current():
    text = (CONTRACTS / "fda_510k.md").read_text()
    with connect() as con:
        n = con.execute("SELECT count(DISTINCT company_name) FROM fda_510k").fetchone()[0]
        rows = con.execute("SELECT count(*) FROM fda_510k").fetchone()[0]
    assert f"rows: {rows} | companies: {n}" in text


def test_contracts_document_every_column():
    """A column the contract never mentions is one the model will misuse."""
    for table, doc in (("pb_deals", "pitchbook.md"), ("pb_companies", "pitchbook.md"),
                       ("fda_510k", "fda_510k.md")):
        text = (CONTRACTS / doc).read_text()
        with connect() as con:
            cols = [
                r[0]
                for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ?",
                    [table],
                ).fetchall()
            ]
        missing = [c for c in cols if f"`{c}`" not in text]
        assert not missing, f"{table}: undocumented columns {missing}"


def test_venture_deal_types_listed_in_contract_match_the_code():
    from fda_agent.ingest_pitchbook import VENTURE_DEAL_TYPES

    text = (CONTRACTS / "pitchbook.md").read_text()
    section = text[text.index("### `is_venture_round`"):]
    for dt in VENTURE_DEAL_TYPES:
        assert f"`{dt}`" in section, f"{dt} not documented"
    # and nothing extra is claimed
    listed = set(re.findall(r"`([A-Z][^`]+)`", section.split("These are primary")[0]))
    assert listed <= set(VENTURE_DEAL_TYPES) | {"is_venture_round"}
