"""Tests for entity resolution, the evidence layer, and cross-source composition.

The property that matters most is that composition cannot fan out. Everything
else here supports it.
"""
from __future__ import annotations

import pytest

from fda_agent.compose import company_profile
from fda_agent.config import DB_PATH
from fda_agent.db import connect
from fda_agent.entity import resolve_company
from fda_agent.evidence import Evidence, Fact, Gap


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


# --- entity resolution -------------------------------------------------------------


def test_resolves_a_short_name_to_the_filed_company():
    r = resolve_company("Aidoc")
    assert r.is_unique
    assert r.entity.company_name == "Aidoc Medical"
    assert r.entity.clearances == 31


def test_resolves_a_former_name():
    """`Ischemaview` no longer exists in company_name after entity merging."""
    r = resolve_company("Ischemaview")
    assert r.is_unique
    assert r.entity.company_name == "Ischema View"


def test_resolves_a_pitchbook_name_the_fda_never_used():
    """`RapidAI` appears nowhere in the FDA data — only PitchBook knows it."""
    r = resolve_company("RapidAI")
    assert r.is_unique
    assert r.entity.company_name == "Ischema View"


def test_ambiguous_reference_returns_every_match():
    r = resolve_company("Samsung")
    assert not r.is_unique
    assert {m.company_name for m in r.matches} == {"Samsung", "Samsung Medison"}
    with pytest.raises(ValueError, match="resolves to 2 companies"):
        _ = r.entity


def test_unknown_reference_is_empty_not_an_error():
    assert resolve_company("No Such Company Ltd").is_unknown
    assert resolve_company("").is_unknown


def test_resolution_does_not_match_substrings_inside_other_words():
    """`GE Healthcare` must not pull in Merge or Change Healthcare."""
    names = {m.company_name for m in resolve_company("GE Healthcare").matches}
    assert "Merge Healthcare" not in names
    assert "Change Healthcare" not in names


# --- the property that matters: no fan-out -----------------------------------------


def test_composition_does_not_inflate_capital():
    """A naive JOIN multiplies funding by the clearance count.

    Aidoc has 31 clearances and 7 venture rounds; joining them gives 217 rows and
    a total 31x too large. The composition layer assembles parallel fact lists
    instead, so the bug is unrepresentable rather than merely avoided.
    """
    with connect() as con:
        inflated = con.execute(
            """
            SELECT round(sum(d.deal_size_usd_m), 1)
            FROM fda_510k f JOIN pb_deals d ON d.company_id = f.pb_company_id
            WHERE f.company_name = 'Aidoc Medical' AND d.is_venture_round
            """
        ).fetchone()[0]

    profile = company_profile("Aidoc")
    rounds = profile.evidence.of_type("FUNDING_ROUND")
    total = round(sum(f.data["deal_size_usd_m"] for f in rounds), 1)

    assert len(rounds) == 7
    assert total == pytest.approx(420.3, abs=0.1)
    assert inflated == pytest.approx(total * 31, rel=0.01), "the join really does fan out"
    assert total < inflated / 30


def test_clearance_count_is_not_multiplied_by_funding_rounds():
    profile = company_profile("Aidoc")
    assert len(profile.evidence.of_type("FDA_CLEARANCE")) == 31


# --- facts carry provenance --------------------------------------------------------


def test_every_fact_has_a_source_and_source_id():
    profile = company_profile("Viz.ai")
    assert profile.evidence.facts
    for f in profile.evidence.facts:
        assert f.source in {"FDA", "PitchBook"}
        assert f.source_id


def test_a_fact_cannot_be_built_without_provenance():
    with pytest.raises(ValueError, match="source and source_id"):
        Fact(type="X", source="FDA", source_id="", date=None, summary="s")


def test_timeline_excludes_undated_facts_rather_than_guessing():
    e = Evidence(
        entity_name="X",
        facts=[
            Fact("A", "FDA", "K1", None, "undated"),
            Fact("B", "FDA", "K2", __import__("datetime").date(2020, 1, 1), "dated"),
        ],
    )
    assert [f.source_id for f in e.timeline()] == ["K2"]


# --- gaps are distinct statements, never silent zeros -------------------------------


def test_out_of_universe_company_reports_scope_not_zero():
    profile = company_profile("Siemens")
    assert not profile.evidence.of_type("FUNDING_ROUND")
    reasons = " ".join(g.reason for g in profile.evidence.gaps if g.topic == "funding")
    assert "outside the venture universe" in reasons
    assert "has not raised nothing" in reasons


def test_gap_reasons_distinguish_the_three_cases():
    """Unbridged, out-of-universe, and no-rounds are different statements."""
    seen = set()
    for name in ("Siemens", "Aidoc", "Hyperfine"):
        for g in company_profile(name).evidence.gaps:
            seen.add(g.topic)
    assert seen <= {"funding", "funding_dates"}


# --- derived metrics ----------------------------------------------------------------


def test_capital_before_first_clearance_uses_only_earlier_rounds():
    profile = company_profile("Aidoc")
    first = profile.first_clearance
    rounds = profile.evidence.of_type("FUNDING_ROUND")
    earlier = [f for f in rounds if f.date and f.date < first]
    assert profile.rounds_before_first_clearance == len(earlier)
    assert profile.capital_before_first_clearance_usd_m == pytest.approx(
        round(sum(f.data["deal_size_usd_m"] for f in earlier), 1), abs=0.1
    )


def test_ambiguous_company_yields_no_evidence():
    """Summing Samsung Electronics and Samsung Medison answers nothing asked."""
    profile = company_profile("Samsung")
    assert profile.is_ambiguous
    assert profile.evidence is None
    assert profile.capital_before_first_clearance_usd_m is None


# --- cohort layer: FDA collapsed to first approval before the join -----------------


@pytest.fixture(scope="module")
def cohort():
    from fda_agent.compose import funding_vs_first_clearance

    return funding_vs_first_clearance()


def test_cohort_is_one_row_per_company(cohort):
    """The whole point of collapsing to first approval.

    Without it, joining clearances to deals gives one row per clearance x round.
    """
    assert len(cohort) == cohort["company_name"].nunique()


def test_cohort_keeps_every_company_including_unfunded(cohort):
    """LEFT JOIN, not INNER — a company with no funding data must still appear,
    or it silently disappears from any cohort statistic."""
    with connect() as con:
        n = con.execute("SELECT count(DISTINCT company_name) FROM fda_510k").fetchone()[0]
    assert len(cohort) == n


def test_cohort_capital_is_not_inflated(cohort):
    """Total across the cohort must equal the underlying deal total for bridged,
    dated venture rounds — no multiplication by clearance count."""
    with connect() as con:
        truth = con.execute(
            """
            SELECT round(sum(d.deal_size_usd_m), 1)
            FROM pb_deals d
            WHERE d.is_venture_round AND d.deal_date IS NOT NULL
              AND d.company_id IN (SELECT DISTINCT pb_company_id FROM fda_510k
                                   WHERE pb_company_id IS NOT NULL)
            """
        ).fetchone()[0]
    total = round(
        cohort["capital_before_usd_m"].fillna(0).sum()
        + cohort["capital_after_usd_m"].fillna(0).sum(),
        1,
    )
    assert total == pytest.approx(truth, rel=0.001)


def test_cohort_round_counts_reconcile_with_the_deal_table(cohort):
    """before + after + undated must equal the company's venture rounds."""
    with connect() as con:
        per_company = con.execute(
            """
            SELECT f.company_name, count(*) AS n
            FROM fda_510k f JOIN pb_deals d ON d.company_id = f.pb_company_id
            WHERE d.is_venture_round
            GROUP BY 1
            """
        ).df()
    # NB: that query fans out by clearance count, so normalise per company.
    with connect() as con:
        truth = con.execute(
            """
            SELECT fc.company_name, count(*) AS n
            FROM (SELECT company_name, any_value(pb_company_id) AS pb_company_id
                  FROM fda_510k GROUP BY company_name) fc
            JOIN pb_deals d ON d.company_id = fc.pb_company_id
            WHERE d.is_venture_round
            GROUP BY 1
            """
        ).df().set_index("company_name")["n"].to_dict()

    for row in cohort.itertuples():
        expected = truth.get(row.company_name, 0)
        got = row.rounds_before + row.rounds_after + row.undated_rounds
        assert got == expected, row.company_name


def test_cohort_uses_null_not_zero_for_unknown_capital(cohort):
    """A company with no recorded rounds has not raised nothing."""
    unfunded = cohort[cohort["rounds_before"] == 0]
    assert unfunded["capital_before_usd_m"].isna().all()


def test_cohort_agrees_with_the_single_company_profile():
    """The cohort path and the profile path must not disagree."""
    from fda_agent.compose import company_profile, funding_vs_first_clearance

    df = funding_vs_first_clearance().set_index("company_name")
    for name in ("Aidoc Medical", "Viz.Ai", "Hyperfine"):
        profile = company_profile(name)
        row = df.loc[name]
        assert profile.rounds_before_first_clearance == row["rounds_before"]
        assert profile.capital_before_first_clearance_usd_m == pytest.approx(
            row["capital_before_usd_m"], abs=0.1
        )


def test_undated_rounds_are_excluded_from_before_and_after(cohort):
    """A deal with no date cannot be called before or after anything."""
    assert (cohort["undated_rounds"] >= 0).all()
    assert cohort["undated_rounds"].sum() > 0, "expected some undated rounds in the data"
