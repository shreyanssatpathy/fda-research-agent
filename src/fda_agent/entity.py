"""Resolve a company reference to a canonical entity.

This is the join key that does not exist in either source. FDA files
`AIDOC MEDICAL , LTD.`; PitchBook says `Aidoc`. Nothing shared connects them
except the owner-supplied bridge, so entity resolution is a step in its own
right rather than a `JOIN ... ON name`.

Deliberately not an LLM call. Given the same string this returns the same entity
every time, which is what makes the evidence layer auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fda_agent.config import DB_PATH
from fda_agent.db import connect

# Anchored at a word boundary, matching contract rule 10. An unanchored
# substring matches `Merge Healthcare` for `GE Healthcare`.
_MATCH = """
lower({col}) = lower(?) OR lower({col}) LIKE lower(?) || ' %'
"""


@dataclass(frozen=True)
class Entity:
    """One resolved company, with the identifiers each source knows it by."""

    company_name: str            # FDA canonical name
    pb_company_id: str | None    # PitchBook identifier, if bridged
    pb_company_name: str | None
    clearances: int
    in_qualified_universe: bool | None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_funding_data(self) -> bool:
        return self.pb_company_id is not None


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving a reference.

    `matches` may hold several entities — that is ambiguity to surface, not a set
    to silently aggregate. Summing Samsung and Samsung Medison answers a question
    nobody asked.
    """

    query: str
    matches: tuple[Entity, ...]

    @property
    def is_unique(self) -> bool:
        return len(self.matches) == 1

    @property
    def is_unknown(self) -> bool:
        return len(self.matches) == 0

    @property
    def entity(self) -> Entity:
        if not self.is_unique:
            raise ValueError(
                f"{self.query!r} resolves to {len(self.matches)} companies; "
                "check is_unique before reading .entity"
            )
        return self.matches[0]


def resolve_company(name: str, db_path: Path = DB_PATH) -> Resolution:
    """Resolve a company reference across both naming systems.

    Searches the FDA canonical name, the raw applicant string (the only record of
    former names after entity merging), and PitchBook's name — then returns whole
    companies, never partial matches.
    """
    name = (name or "").strip()
    if not name:
        return Resolution(query=name, matches=())

    sql = f"""
    WITH matched AS (
        SELECT DISTINCT company_name
        FROM fda_510k
        WHERE ({_MATCH.format(col='company_name')})
           OR ({_MATCH.format(col='applicant_raw')})
           OR lower(applicant_raw) LIKE lower(?) || ',%'
        UNION
        SELECT DISTINCT f.company_name
        FROM fda_510k f
        JOIN pb_companies c ON c.company_id = f.pb_company_id
        WHERE ({_MATCH.format(col='c.company_name_pb')})
           OR ({_MATCH.format(col='c.company_former_name')})
    )
    SELECT f.company_name,
           any_value(f.pb_company_id)        AS pb_company_id,
           any_value(c.company_name_pb)      AS pb_company_name,
           count(*)                          AS clearances,
           any_value(c.in_qualified_universe) AS in_qualified_universe,
           list(DISTINCT f.applicant_raw)    AS aliases
    FROM fda_510k f
    LEFT JOIN pb_companies c ON c.company_id = f.pb_company_id
    WHERE f.company_name IN (SELECT company_name FROM matched)
    GROUP BY f.company_name
    ORDER BY clearances DESC, f.company_name
    """
    params = [name] * 9

    with connect(db_path) as con:
        rows = con.execute(sql, params).df()

    matches = tuple(
        Entity(
            company_name=r.company_name,
            pb_company_id=None if r.pb_company_id is None else str(r.pb_company_id),
            pb_company_name=None if r.pb_company_name is None else str(r.pb_company_name),
            clearances=int(r.clearances),
            in_qualified_universe=(
                None if r.in_qualified_universe is None else bool(r.in_qualified_universe)
            ),
            aliases=tuple(sorted(r.aliases)),
        )
        for r in rows.itertuples()
    )
    return Resolution(query=name, matches=matches)
