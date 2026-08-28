"""The evidence layer.

CLAUDE.md: the evidence layer determines facts, the LLM writes the narrative. A
`Fact` therefore always carries where it came from and which record it came from,
and there is no way to construct one without them.

Facts are assembled per source and keyed by entity — they are never produced by
joining rows across sources. A company with 12 clearances and 4 funding rounds
joins to 48 rows, and `sum(deal_size)` over that is 12x the real figure. The SQL
guard cannot catch it: it validates table names and functions, not cardinality.
Assembling parallel lists makes the bug unrepresentable rather than merely
avoided.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Fact:
    """One sourced statement. No fact exists without `source` and `source_id`."""

    type: str          # FDA_CLEARANCE | FUNDING_ROUND | COMPANY_ATTRIBUTE
    source: str        # FDA | PitchBook
    source_id: str     # regnumber | deal_id | company_id
    date: date | None
    summary: str
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source or not self.source_id:
            raise ValueError("a fact must carry both source and source_id")


@dataclass(frozen=True)
class Gap:
    """Something the answer cannot cover, and why.

    Gaps are first-class rather than silent omissions. "No funding rows" and
    "outside the venture universe" are different statements, and neither is
    "raised nothing".
    """

    topic: str
    reason: str


@dataclass
class Evidence:
    """Everything known about one entity, with its gaps."""

    entity_name: str
    facts: list[Fact] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    def of_type(self, kind: str) -> list[Fact]:
        return [f for f in self.facts if f.type == kind]

    @property
    def sources(self) -> set[str]:
        return {f.source for f in self.facts}

    def timeline(self) -> list[Fact]:
        """Dated facts in order. Undated facts are excluded, not guessed at."""
        return sorted(
            (f for f in self.facts if f.date is not None), key=lambda f: f.date
        )
