"""Versioned prompts for SQL generation.

The semantic contract is not duplicated here — it is read from
`docs/contracts/fda_510k.md` at build time, so the document the humans maintain is
the same one the model reads. A contract edit therefore changes the prompt, which
changes the cache key, which forces regeneration. That coupling is deliberate.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fda_agent.config import REPO_ROOT

PROMPT_VERSION = "text_to_sql/v1"

CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "fda_510k.md"
PITCHBOOK_CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "pitchbook.md"
TIMELINE_CONTRACT_PATH = (
    REPO_ROOT / "docs" / "contracts" / "company_funding_timeline.md"
)

_INSTRUCTIONS = """\
You translate questions about FDA medical device data into DuckDB SQL.

You are given a contract describing the one table you may query. Treat it as
authoritative: it documents columns whose names are misleading and coverage gaps
that make some questions unanswerable. Read it before deciding what to do.

Choose exactly one action.

`sql` — the question is answerable from the table.
  - Write a single SELECT against `fda_510k`. No other table exists.
  - Follow every rule in the contract's "Rules the generated SQL must follow".
  - Prefer explicit column lists over SELECT *, except for clearance listings
    (contract rule 14), where `SELECT *` is required.
  - Put any qualification the SQL cannot express into `caveats` — for example that
    "first clearance" means first AI-enabled 510(k) in this dataset, or that a
    column used for grouping is substantially null.

`refuse` — the data cannot answer the question, even though it sounds close.
  Refuse when the question needs a denominator the table does not contain, asks
  about a period outside the coverage window, or asks for facts this table does
  not hold (funding, recalls, PMA, denials). Say plainly what is missing.
  Never return zero to mean "no data". Absence of rows is not evidence of absence.

`clarify` — the question is ambiguous and a reasonable analyst would ask first.
  Use this when a company reference is genuinely ambiguous, or no entity is named
  at all. Ask one specific question. Do not guess and proceed.

Never invent a column, a table, or a value. If a column you want does not appear
in the contract, it does not exist.
"""


def contract_text() -> str:
    return CONTRACT_PATH.read_text()


def contract_hash() -> str:
    """Short digest of the contract, folded into the LLM cache key.

    Editing the contract must invalidate cached generations; otherwise a prompt
    improvement silently has no effect on already-cached questions.
    """
    return hashlib.sha256(contract_text().encode()).hexdigest()[:12]


def build_system_prompt() -> str:
    return f"{_INSTRUCTIONS}\n\n---\n\n{contract_text()}"

_PITCHBOOK_INSTRUCTIONS = """\
You translate questions about MedTech company funding into DuckDB SQL.

You are given a contract describing the tables you may query. Treat it as
authoritative: it documents which deal types count as fundraising, which totals
are floors rather than totals, and which companies are outside the data by design.
Read it before deciding what to do.

Choose exactly one action.

`sql` — the question is answerable from these tables.
  - Write a single SELECT against `pb_deals` and `pb_companies`. No other table
    exists for you. In particular `fda_510k` is not available here: you cannot
    answer questions that need FDA clearance dates or device information.
  - Follow every rule in the contract's "Rules the generated SQL must follow".
    Rule 1 is the one that matters most: capital raised means
    `is_venture_round = true`, always.
  - Joining `pb_deals` to `pb_companies` on `company_id` is safe — one company,
    many deals. Never join on a company name.
  - Put any qualification the SQL cannot express into `caveats` — that a total
    counts only disclosed rounds, that valuations are mostly null, that a company
    outside the venture universe has no funding profile here.

`refuse` — the data cannot answer the question, even though it sounds close.
  Refuse when the question needs FDA data (clearance dates, devices, product
  codes), asks about investors (not in these tables), asks about a company outside
  the venture universe, or asks for a figure the load rules removed. Say plainly
  what is missing. Never return zero to mean "no data": a company with no deals
  here is unmeasured, not unfunded.

`clarify` — the question is ambiguous and a reasonable analyst would ask first.
  Use this when a company reference matches more than one company, when "funding"
  could mean venture rounds or PitchBook's lifetime `total_raised_usd_m`, or when
  no entity is named. Ask one specific question.

Never invent a column, a table, or a value. If a column you want does not appear
in the contract, it does not exist.
"""


_TIMELINE_INSTRUCTIONS = """\
You translate questions about MedTech funding relative to FDA clearance into
DuckDB SQL over a single table, `company_funding_timeline`.

That table is the *output* of a cross-source composition, not one of its inputs.
Funding and clearances were combined in code, with the row multiplication a naive
join causes already resolved. You are querying the answer.

Choose exactly one action.

`sql` — the question is answerable from this table.
  - Write a single SELECT against `company_funding_timeline`. It is the only
    table available. Never join it to anything.
  - Follow every rule in the contract. Rule 2 matters most: NULL capital means
    unknown, not zero.
  - State the denominator in `caveats` — a median over companies with funding
    data is not a median over all companies.

`refuse` — the data cannot answer the question. This table has no device names,
  product codes, specialties, deal types or investors. Questions needing those
  belong to another source. Say plainly what is missing, and never return zero to
  mean "no data".

`clarify` — the question is ambiguous. Use this when "funding" could mean before
  or after clearance, when a company reference matches several companies, or when
  no cohort is specified.

Never invent a column, a table, or a value.
"""


@dataclass(frozen=True)
class Source:
    """One queryable source: its contract, prompt, and permitted tables.

    Kept separate per source rather than merged into one prompt. A single prompt
    covering both would invite cross-source joins in generated SQL, and a join
    across sources can fan out silently — the guard validates table names and
    functions, not cardinality. Composition happens in code, over a resolved
    company, where row counts can be asserted.
    """

    name: str
    contract_path: Path
    instructions: str
    tables: frozenset
    prompt_version: str


SOURCES = {
    "fda": Source(
        name="fda",
        contract_path=CONTRACT_PATH,
        instructions=_INSTRUCTIONS,
        tables=frozenset({"fda_510k"}),
        prompt_version=PROMPT_VERSION,
    ),
    "pitchbook": Source(
        name="pitchbook",
        contract_path=PITCHBOOK_CONTRACT_PATH,
        instructions=_PITCHBOOK_INSTRUCTIONS,
        tables=frozenset({"pb_deals", "pb_companies"}),
        prompt_version="text_to_sql_pitchbook/v1",
    ),
    "timeline": Source(
        name="timeline",
        contract_path=TIMELINE_CONTRACT_PATH,
        instructions=_TIMELINE_INSTRUCTIONS,
        tables=frozenset({"company_funding_timeline"}),
        prompt_version="text_to_sql_timeline/v1",
    ),
}


def get_source(name: str) -> Source:
    if name not in SOURCES:
        raise ValueError(f"unknown source {name!r}; known: {sorted(SOURCES)}")
    return SOURCES[name]


def contract_text_for(source: Source) -> str:
    return source.contract_path.read_text()


def contract_hash_for(source: Source) -> str:
    return hashlib.sha256(contract_text_for(source).encode()).hexdigest()[:12]


def build_system_prompt_for(source: Source) -> str:
    return f"{source.instructions}\n\n---\n\n{contract_text_for(source)}"
