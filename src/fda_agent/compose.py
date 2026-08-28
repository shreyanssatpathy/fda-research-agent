"""Cross-source composition.

The join happens here, in Python, over a resolved entity — never in generated
SQL. Three reasons, in order of how much they cost when ignored:

1. **Fan-out is silent.** Joining clearances to funding rounds multiplies rows,
   and a `SUM` over the result is wrong by the fan-out factor with nothing to
   show for it.
2. **The guard cannot see it.** It validates tables and functions, not
   cardinality.
3. **Match confidence has nowhere to live in a JOIN.** A row is included or it
   is not; the uncertainty disappears from the result.

Each source is queried separately with its own scoped SQL, and the results are
assembled into `Evidence` keyed by the entity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fda_agent.config import DB_PATH
from fda_agent.db import connect
from fda_agent.entity import Entity, Resolution, resolve_company
from fda_agent.evidence import Evidence, Fact, Gap

# Deal types counting as capital raised live in the loader; this mirrors the
# contract rule rather than re-deriving it.
VENTURE_FILTER = "is_venture_round"


# The analytical unit for funding-versus-approval questions: one row per company,
# anchored on its first clearance.
#
# Collapsing FDA to first approval before joining deals is the standard way to
# make this join safe — the FDA side becomes one row, so the join is 1:many and
# SUM(deal_size) cannot double-count. It is the same guarantee the per-company
# fact lists give, expressed as SQL, and it is what makes cohort questions
# ("companies that raised under $50m before clearance") answerable at all.
FIRST_CLEARANCE_CTE = """
first_clearance AS (
    SELECT company_name,
           any_value(pb_company_id)  AS pb_company_id,
           min(decision_date)        AS first_clearance,
           count(*)                  AS total_clearances
    FROM fda_510k
    GROUP BY company_name
)
"""


@dataclass
class Profile:
    """A company research profile assembled from every available source."""

    resolution: Resolution
    evidence: Evidence | None = None
    capital_before_first_clearance_usd_m: float | None = None
    rounds_before_first_clearance: int = 0
    first_clearance: date | None = None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.resolution.matches) > 1

    @property
    def is_unknown(self) -> bool:
        return self.resolution.is_unknown


def _fda_facts(entity: Entity, con) -> list[Fact]:
    rows = con.execute(
        """
        SELECT regnumber, decision_date, device_trade_name, product_code,
               medical_specialty, submission_type
        FROM fda_510k WHERE company_name = ? ORDER BY decision_date
        """,
        [entity.company_name],
    ).df()
    return [
        Fact(
            type="FDA_CLEARANCE",
            source="FDA",
            source_id=r.regnumber,
            date=r.decision_date,
            summary=f"510(k) clearance for {r.device_trade_name} ({r.product_code})",
            data={
                "device_trade_name": r.device_trade_name,
                "product_code": r.product_code,
                "medical_specialty": r.medical_specialty,
                "submission_type": r.submission_type,
            },
        )
        for r in rows.itertuples()
    ]


def _funding_facts(entity: Entity, con) -> list[Fact]:
    if not entity.pb_company_id:
        return []
    rows = con.execute(
        f"""
        SELECT deal_id, deal_date, deal_type, deal_size_usd_m, deal_size_status
        FROM pb_deals
        WHERE company_id = ? AND {VENTURE_FILTER}
        ORDER BY deal_date
        """,
        [entity.pb_company_id],
    ).df()
    return [
        Fact(
            type="FUNDING_ROUND",
            source="PitchBook",
            source_id=r.deal_id,
            date=None if r.deal_date is None or r.deal_date != r.deal_date else r.deal_date,
            summary=f"{r.deal_type}, ${r.deal_size_usd_m:,.1f}m",
            data={
                "deal_type": r.deal_type,
                "deal_size_usd_m": float(r.deal_size_usd_m),
                "size_status": r.deal_size_status,
            },
        )
        for r in rows.itertuples()
    ]


def _gaps(entity: Entity, funding: list[Fact], con) -> list[Gap]:
    """Say what is missing and why. Every branch here is a different statement."""
    gaps: list[Gap] = []

    if entity.pb_company_id is None:
        gaps.append(
            Gap(
                "funding",
                "This company is not in the FDA-to-PitchBook bridge, so no funding "
                "data is available. That is missing data, not an absence of funding.",
            )
        )
    elif entity.in_qualified_universe is False:
        gaps.append(
            Gap(
                "funding",
                "PitchBook classifies this company outside the venture universe "
                "(a corporation rather than a venture-backed firm), so it has no "
                "funding profile here by design. It has not raised nothing — it is "
                "out of scope.",
            )
        )
    elif not funding:
        gaps.append(
            Gap(
                "funding",
                "No venture rounds with a disclosed size are recorded for this "
                "company. Undisclosed rounds are excluded at load, so this is a "
                "floor of zero, not a confirmed zero.",
            )
        )

    undated = [f for f in funding if f.date is None]
    if undated:
        gaps.append(
            Gap(
                "funding_dates",
                f"{len(undated)} funding round(s) have no date and are excluded "
                "from any before/after comparison.",
            )
        )
    return gaps


def company_profile(name: str, db_path: Path = DB_PATH) -> Profile:
    """Assemble everything known about one company across both sources.

    Returns without evidence when the reference is unknown or ambiguous —
    resolving `Samsung` by summing Samsung Electronics and Samsung Medison would
    answer a question nobody asked.
    """
    resolution = resolve_company(name, db_path)
    if not resolution.is_unique:
        return Profile(resolution=resolution)

    entity = resolution.entity
    with connect(db_path) as con:
        fda = _fda_facts(entity, con)
        funding = _funding_facts(entity, con)
        gaps = _gaps(entity, funding, con)

    evidence = Evidence(entity_name=entity.company_name, facts=fda + funding, gaps=gaps)

    first_clearance = min((f.date for f in fda if f.date), default=None)
    before = [
        f for f in funding if f.date is not None and first_clearance and f.date < first_clearance
    ]
    capital = round(sum(f.data["deal_size_usd_m"] for f in before), 1) if before else None

    return Profile(
        resolution=resolution,
        evidence=evidence,
        capital_before_first_clearance_usd_m=capital,
        rounds_before_first_clearance=len(before),
        first_clearance=first_clearance,
    )


def funding_vs_first_clearance(db_path: Path = DB_PATH):
    """One row per company: funding raised before and after its first clearance.

    The cohort-level counterpart to `company_profile`. FDA is collapsed to first
    approval *before* the join, so each company contributes exactly one FDA row
    and deal sizes cannot be multiplied by clearance count.

    Undated deals are excluded from both sides and counted separately — a deal
    that cannot be placed in time cannot be called "before" or "after".

    Columns whose value is unknown stay NULL rather than becoming 0: a company
    with no recorded rounds has not raised nothing.
    """
    sql = f"""
    WITH {FIRST_CLEARANCE_CTE},
    deals AS (
        SELECT company_id, deal_date, deal_size_usd_m
        FROM pb_deals
        WHERE is_venture_round AND deal_date IS NOT NULL
    ),
    undated AS (
        SELECT company_id, count(*) AS undated_rounds
        FROM pb_deals WHERE is_venture_round AND deal_date IS NULL
        GROUP BY company_id
    )
    SELECT fc.company_name,
           fc.pb_company_id,
           fc.first_clearance,
           fc.total_clearances,
           count(d.deal_date) FILTER (WHERE d.deal_date <  fc.first_clearance) AS rounds_before,
           round(sum(d.deal_size_usd_m) FILTER (WHERE d.deal_date <  fc.first_clearance), 1) AS capital_before_usd_m,
           count(d.deal_date) FILTER (WHERE d.deal_date >= fc.first_clearance) AS rounds_after,
           round(sum(d.deal_size_usd_m) FILTER (WHERE d.deal_date >= fc.first_clearance), 1) AS capital_after_usd_m,
           coalesce(any_value(u.undated_rounds), 0) AS undated_rounds
    FROM first_clearance fc
    LEFT JOIN deals d ON d.company_id = fc.pb_company_id
    LEFT JOIN undated u ON u.company_id = fc.pb_company_id
    GROUP BY 1, 2, 3, 4
    ORDER BY fc.company_name
    """
    with connect(db_path) as con:
        return con.execute(sql).df()
