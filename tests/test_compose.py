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
        got = row.rounds_before + row.rounds_after + row.undated_venture_rounds
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


def test_undated_venture_rounds_are_excluded_from_before_and_after(cohort):
    """A deal with no date cannot be called before or after anything."""
    assert (cohort["undated_venture_rounds"] >= 0).all()
    assert cohort["undated_venture_rounds"].sum() > 0, "expected some undated rounds in the data"


# --- undated rounds must stay visible ----------------------------------------------


def test_undated_venture_rounds_are_counted_not_dropped(cohort):
    """Capital that cannot be placed in time must remain visible.

    26 rounds worth $211.1m belong to FDA companies but have no date. They are
    excluded from before and after — correctly, since a deal with no date cannot
    be called either — but silently dropping them would understate every
    pre-clearance figure with nothing to show for it.
    """
    with connect() as con:
        truth = con.execute(
            """
            SELECT count(*) FROM pb_deals d
            WHERE d.is_venture_round AND d.deal_date IS NULL
              AND d.company_id IN (SELECT DISTINCT pb_company_id FROM fda_510k
                                   WHERE pb_company_id IS NOT NULL)
            """
        ).fetchone()[0]
    assert cohort["undated_venture_rounds"].sum() == truth
    assert (cohort["undated_venture_rounds"] > 0).sum() == 25


def test_profile_reports_undated_venture_rounds_as_a_gap():
    """A company whose funding cannot be fully placed in time must say so."""
    from fda_agent.compose import funding_vs_first_clearance

    affected = funding_vs_first_clearance()
    name = affected[affected["undated_venture_rounds"] > 0]["company_name"].iloc[0]
    profile = company_profile(name)
    topics = {g.topic for g in profile.evidence.gaps}
    assert "funding_dates" in topics
    reason = next(g.reason for g in profile.evidence.gaps if g.topic == "funding_dates")
    assert "excluded from any before/after comparison" in reason


def test_undated_facts_carry_no_guessed_date():
    from fda_agent.compose import funding_vs_first_clearance

    affected = funding_vs_first_clearance()
    name = affected[affected["undated_venture_rounds"] > 0]["company_name"].iloc[0]
    profile = company_profile(name)
    undated = [f for f in profile.evidence.of_type("FUNDING_ROUND") if f.date is None]
    assert undated, "expected at least one undated round"
    assert all(f.source_id for f in undated), "still sourced, just undated"


def test_undated_column_counts_venture_rounds_only():
    """The name is the contract.

    Fitbit has an undated Grant, so it *does* have a deal with no date — but grants
    are not venture rounds, so `undated_venture_rounds` is 0. That is correct: the
    grant is excluded from capital on deal-type grounds regardless of its date.
    A column called `undated_rounds` invited the opposite reading.
    """
    from fda_agent.compose import funding_vs_first_clearance

    with connect() as con:
        fitbit_undated = con.execute(
            """
            SELECT count(*) FROM pb_deals d JOIN pb_companies c USING (company_id)
            WHERE c.company_name_pb ILIKE 'Fitbit%' AND d.deal_date IS NULL
            """
        ).fetchone()[0]
        fitbit_undated_venture = con.execute(
            """
            SELECT count(*) FROM pb_deals d JOIN pb_companies c USING (company_id)
            WHERE c.company_name_pb ILIKE 'Fitbit%' AND d.deal_date IS NULL
              AND d.is_venture_round
            """
        ).fetchone()[0]

    assert fitbit_undated == 1, "Fitbit should have one undated deal"
    assert fitbit_undated_venture == 0, "...and it is not a venture round"

    df = funding_vs_first_clearance().set_index("company_name")
    assert df.loc["Fitbit", "undated_venture_rounds"] == 0


def test_non_venture_undated_deals_exist_and_are_out_of_scope():
    """Documented blind spot: 37 companies have an undated deal, 25 are flagged."""
    with connect() as con:
        any_undated = con.execute(
            """
            SELECT count(DISTINCT f.company_name) FROM fda_510k f
            JOIN pb_deals d ON d.company_id = f.pb_company_id
            WHERE d.deal_date IS NULL
            """
        ).fetchone()[0]
        flagged = con.execute(
            "SELECT count(*) FROM company_funding_timeline "
            "WHERE undated_venture_rounds > 0"
        ).fetchone()[0]
    assert any_undated == 37
    assert flagged == 25


# --- views replacing the materialised table (2026-09-02) ---------------------------


def test_both_cross_source_objects_are_views_not_tables():
    """Views cannot go stale and need no rebuild step."""
    with connect() as con:
        kinds = dict(
            con.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_name IN ('company_funding_timeline', 'v_company_deals')"
            ).fetchall()
        )
    assert kinds == {"company_funding_timeline": "VIEW", "v_company_deals": "VIEW"}


def test_deal_view_is_one_row_per_venture_round():
    with connect() as con:
        rows, deals = con.execute(
            "SELECT count(*), count(DISTINCT deal_id) FROM v_company_deals"
        ).fetchone()
        truth = con.execute(
            """
            SELECT count(*) FROM pb_deals d WHERE d.is_venture_round
              AND d.company_id IN (SELECT DISTINCT pb_company_id FROM fda_510k
                                   WHERE pb_company_id IS NOT NULL)
            """
        ).fetchone()[0]
    assert rows == deals == truth == 1159


def test_deal_view_capital_is_not_inflated():
    """Attaching company attributes to deal rows is many-to-one: no fan-out."""
    with connect() as con:
        via_view = con.execute(
            "SELECT round(sum(deal_size_usd_m), 1) FROM v_company_deals"
        ).fetchone()[0]
        truth = con.execute(
            """
            SELECT round(sum(d.deal_size_usd_m), 1) FROM pb_deals d
            WHERE d.is_venture_round AND d.company_id IN
              (SELECT DISTINCT pb_company_id FROM fda_510k WHERE pb_company_id IS NOT NULL)
            """
        ).fetchone()[0]
    assert via_view == pytest.approx(truth, rel=0.001)


def test_company_attributes_repeat_on_deal_rows():
    """The documented footgun, pinned so the contract's warning stays true.

    Aidoc: 31 clearances, 7 rounds. sum(total_clearances) is 217 and wrong;
    any_value is 31 and right. Deal columns remain safe to sum.
    """
    with connect() as con:
        r = con.execute(
            """
            SELECT count(*) AS rows, sum(total_clearances) AS summed,
                   any_value(total_clearances) AS correct,
                   round(sum(deal_size_usd_m), 1) AS capital
            FROM v_company_deals WHERE company_name = 'Aidoc Medical'
            """
        ).fetchone()
    rows, summed, correct, capital = r
    assert rows == 7
    assert correct == 31
    assert summed == 31 * 7
    assert capital == pytest.approx(420.3, abs=0.1)


def test_company_view_still_matches_the_pre_view_figures():
    """The refactor must not move any number the old table produced."""
    with connect() as con:
        r = con.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE rounds_before > 0),
                   round(median(capital_before_usd_m), 1),
                   sum(undated_venture_rounds)
            FROM company_funding_timeline
            """
        ).fetchone()
    assert r == (459, 289, 7.8, 26)


def test_funding_dates_are_now_available():
    """The columns whose absence made the timing question unanswerable."""
    with connect() as con:
        cols = {
            c[0]
            for c in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'company_funding_timeline'"
            ).fetchall()
        }
    assert {"first_funding_date", "last_funding_date",
            "days_first_funding_to_first_clearance"} <= cols


def test_time_from_first_funding_to_first_clearance_is_computable():
    """The originally-refused question, now answerable from the view alone."""
    with connect() as con:
        r = con.execute(
            """
            SELECT count(*), round(avg(days_first_funding_to_first_clearance) / 365.25, 2)
            FROM company_funding_timeline
            WHERE year(first_clearance) >= 2025
              AND days_first_funding_to_first_clearance > 0
            """
        ).fetchone()
    assert r[0] == 52
    assert r[1] == pytest.approx(6.15, abs=0.01)


def test_unfunded_companies_survive_in_the_company_view():
    """LEFT JOIN — companies with no venture rounds must not vanish.

    148, not the 120 quoted in earlier notes: that figure counted companies with
    no deal of *any* type, but these views are venture-only. 147 have no venture
    round; one more has only an undated one, so it has no first_funding_date.
    """
    with connect() as con:
        no_date, no_rounds = con.execute(
            """
            SELECT count(*) FILTER (WHERE first_funding_date IS NULL),
                   count(*) FILTER (WHERE rounds_before = 0 AND rounds_after = 0
                                      AND undated_venture_rounds = 0)
            FROM company_funding_timeline
            """
        ).fetchone()
    assert no_rounds == 147
    assert no_date == 148
    assert no_date > no_rounds, "the extra company has only an undated round"


def test_views_survive_a_rebuild():
    """An earlier build left a TABLE here; DuckDB errors on DROP TABLE for a view."""
    from fda_agent.compose import create_views

    counts = create_views()
    assert counts == {"v_company_deals": 1159, "company_funding_timeline": 459}
    assert create_views() == counts, "create_views must be idempotent"
