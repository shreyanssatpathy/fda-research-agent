"""Versioned prompts for SQL generation.

The semantic contract is not duplicated here — it is read from
`docs/contracts/fda_510k.md` at build time, so the document the humans maintain is
the same one the model reads. A contract edit therefore changes the prompt, which
changes the cache key, which forces regeneration. That coupling is deliberate.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fda_agent.config import REPO_ROOT

PROMPT_VERSION = "text_to_sql/v1"

CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "fda_510k.md"

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
