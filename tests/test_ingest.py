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


def test_dates_are_stored_as_date_type():
    """Check the stored SQL type, not the pandas view.

    Converting with pd.to_datetime first would pass on VARCHAR columns and hide
    the defect; generated SQL calling year() or date_diff() would then fail.
    """
    with connect() as con:
        types = dict(
            con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'fda_510k'"
            ).fetchall()
        )
    assert types["decision_date"] == "DATE"
    assert types["date_received"] == "DATE"


def test_date_functions_work_in_sql():
    """The failure this guards against is generated SQL, not the loader."""
    with connect() as con:
        n = con.execute(
            "SELECT count(*) FROM fda_510k WHERE year(decision_date) = 2023"
        ).fetchone()[0]
        assert n > 0
        assert con.execute(
            "SELECT max(date_diff('day', date_received, decision_date)) FROM fda_510k"
        ).fetchone()[0] > 0


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

    Only SAMSUNG ELECTRONICS remains split, and legitimately: PitchBook holds
    Samsung Electronics and Samsung Medison as different companies, so the one
    applicant string really does span two firms. Anything else is a regression.
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



# --- identity resolution via the PitchBook bridge (2026-08-27) ----------------------


def test_bridge_merged_split_companies(rows):
    """Names the FDA data could not tell apart, resolved by external evidence.

    `Ischema View` / `Ischemaview` and `Bay Labs` / `Caption Health` are one
    company each. Nothing inside the FDA extract distinguishes those from two
    genuinely different firms — the PitchBook company ID does.
    """
    names = set(rows["company_name"])
    for gone in ("Ischemaview", "Caption Health", "Iterative Scopes", "Software Nemotec",
                 "Aiq Solutions", "Corvista Health", "Medicrea International"):
        assert gone not in names, f"{gone} should have been merged away"


def test_merged_company_keeps_the_most_common_fda_spelling(rows):
    """Not PitchBook's current name — users type what appears in FDA filings, and
    contract rule 10 matches against this column."""
    assert "Ischema View" in set(rows["company_name"])
    assert "RapidAI" not in set(rows["company_name"])
    assert (rows["company_name"] == "Ischema View").sum() == 20


def test_parent_subsidiary_rollups_are_not_merged(rows):
    """GE and Fujifilm subsidiaries file separately; collapsing them is a corporate
    hierarchy decision, not a name-variant fix. Pending owner ruling."""
    names = set(rows["company_name"])
    assert {"GE Healthcare", "Ge Hangwei Medical Systems"} <= names
    assert {"Fujifilm", "Fujifilm Healthcare"} <= names


def test_bridge_coverage_is_recorded(rows):
    assert rows["pb_company_id"].notna().sum() == 1359
    assert rows["pb_company_id"].isna().sum() == 8



def test_tie_break_prefers_pitchbooks_spelling():
    """When two spellings appear equally often, PitchBook's current name decides.

    Breaking ties by whichever pandas saw first picked `Corvista Health` over
    `CorVista Health` and `Software Nemotec` over `Nemotec`.
    """
    import pandas as pd

    from fda_agent.ingest import _pick_canonical

    names = pd.Series(["Corvista Health", "CorVista Health"])
    assert _pick_canonical(names, "CorVista Health") == "CorVista Health"
    assert _pick_canonical(names, None) == "CorVista Health"  # alphabetical fallback


def test_frequency_beats_the_tie_break():
    """A clear majority spelling wins even if PitchBook calls the company
    something else — most filings use it, so it is what people will search."""
    import pandas as pd

    from fda_agent.ingest import _pick_canonical

    names = pd.Series(["Bay Labs"] * 4 + ["Caption Health"] * 2)
    assert _pick_canonical(names, "Caption Care") == "Bay Labs"


def test_rebuilding_fda_preserves_pitchbook_tables():
    """The FDA loader must not delete the database file.

    It used to, which silently destroyed pb_deals, pb_companies and
    fda_pb_bridge on every rebuild. The PitchBook tests skip when their tables
    are missing, so the damage surfaced as *skipped* tests rather than failures —
    the quietest possible way to lose data.
    """
    with connect() as con:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
    assert {"fda_510k", "pb_deals", "pb_companies", "fda_pb_bridge"} <= tables


def test_fda_rebuild_keeps_pitchbook_metadata():
    """pb_* metadata rows belong to the other loader and must survive."""
    with connect() as con:
        keys = {
            r[0] for r in con.execute("SELECT key FROM ingest_metadata").fetchall()
        }
    assert any(k.startswith("pb_") for k in keys)
    assert "schema_version" in keys


def test_former_names_remain_findable_via_applicant_raw():
    """Merging must not make a company unfindable by its former name.

    Entity resolution rewrites company_name, so `Ischemaview`, `Caption Health`,
    `Kico Knee Innovation` and `Heartvista` no longer appear there. applicant_raw
    is never rewritten, which is what contract rule 10 relies on.
    """
    sql = """
    SELECT count(*) FROM fda_510k WHERE company_name IN (
      SELECT DISTINCT company_name FROM fda_510k
      WHERE lower(company_name)   = lower(?)  OR lower(company_name)  LIKE lower(?) || ' %'
         OR lower(applicant_raw)  = lower(?)  OR lower(applicant_raw) LIKE lower(?) || ' %'
         OR lower(applicant_raw) LIKE lower(?) || ',%')
    """
    with connect() as con:
        for name, expected in [
            ("Ischemaview", 20), ("Ischema View", 20),   # former and surviving name
            ("Caption Health", 6), ("Bay Labs", 6),      # both resolve to one company
            ("Kico Knee Innovation", 2), ("Heartvista", 2),
        ]:
            got = con.execute(sql, [name] * 5).fetchone()[0]
            assert got == expected, f"{name}: got {got}, expected {expected}"


def test_company_match_still_excludes_substring_false_positives():
    """The anchored form must not reintroduce the Merge/Change Healthcare bug."""
    sql = """
    SELECT count(*) FROM fda_510k WHERE company_name IN (
      SELECT DISTINCT company_name FROM fda_510k
      WHERE lower(company_name)   = lower(?)  OR lower(company_name)  LIKE lower(?) || ' %'
         OR lower(applicant_raw)  = lower(?)  OR lower(applicant_raw) LIKE lower(?) || ' %'
         OR lower(applicant_raw) LIKE lower(?) || ',%')
    """
    with connect() as con:
        assert con.execute(sql, ["GE Healthcare"] * 5).fetchone()[0] == 95
        assert con.execute(sql, ["Merge Healthcare"] * 5).fetchone()[0] == 1
