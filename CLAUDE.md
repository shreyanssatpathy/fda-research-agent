# Project invariants

Read this first, every session. Anything here must survive a context reset.

The phase-by-phase plan lives in `docs/PLAN.md` and is the shape of the project.
This file is the set of rules that plan must be built under.

## What this is

An agentic MedTech research system: natural-language questions answered from FDA
device data, investment data, and public sources, with traceable citations.
Text-to-SQL is one component, not the product.

Build order is fixed (`docs/PLAN.md` §17). Do not skip ahead:

```
FDA text-to-SQL -> reliable FDA copilot -> investment data -> entity resolution
-> cross-database research -> web research -> full agent
```

## V1 scope (decided 2026-08-27)

V1 runs on **AI-enabled medical device 510(k) clearances, 2010 onwards** — a
pre-filtered extract supplied by the owner, not the full FDA corpus.

Two consequences that must be enforced in the semantic layer, not left to the LLM:

- "First approval" means **first AI-enabled 510(k) clearance in this dataset**.
  It is not the company's first FDA clearance — earlier non-AI clearances exist
  and are absent here. Every answer must say the narrower thing.
- **Share-of-total questions have no denominator** and must be refused, not
  computed. There is no non-AI baseline in the data.

Expansion order once V1 is reliable: other submission types (PMA, De Novo), then
non-AI devices, then PitchBook. Widening the data does not retire the rules above
until the data actually covers the wider claim.

## Non-negotiables

### Evidence
- The evidence layer determines facts. The LLM writes the narrative, never the
  facts. No fact reaches synthesis without a `source` and a `source_id`.
- Never invent a date, amount, or identifier. Missing is reported as missing.
- Conflicting sources are surfaced as conflicts, never silently reconciled to one
  value. Keep every source's version.

### SQL
- Generated SQL is SELECT-only, executed as a read-only database user, against a
  table allowlist, with a row limit and a statement timeout. All five, always.
- Validation is a parser, not a regex, and not the prompt.
- Every generated query is logged with the question that produced it.

### LLM
- Every LLM call is content-hash cached. No uncached call inside a row loop.
- Hard spend ceiling enforced in code (`src/llm/budget.py`), not by prompt.
- Every script defaults to sample mode; full runs require an explicit flag.
- Every generated artifact carries `prompt_version`, `model_id`, `schema_version`.

### Evaluation
- The golden eval set is frozen. Never regenerate, edit, or extend it to make a
  score improve. Report the gap instead.
- Never modify a test to make it pass. Stop and report.

### Data
- Raw source data is never committed or redistributed. `data/` is gitignored and
  stays that way. The repo holds code and derived aggregates only.
- Ingested raw records are append-only. Never rewrite or delete them.

### Process
- Contracts in `docs/contracts/` are authoritative. Amend explicitly with a dated
  note; never diverge silently.
- Done means the task's acceptance command exits 0. Not "looks right".
- One commit per task, task ID in the message.

## Source-selection rule

This repo is public and shown to interviewers. Any source used must have terms
that clearly permit the processing this system does, and the permission must be
citable in the README. "Probably fine" does not qualify — the licensing note is
part of what the project is demonstrating.

Record every source decision here, including the ones ruled out, so they are not
relitigated later.

## Source status

### FDA device data — NOT YET VERIFIED

Phase 0 blocker. Before any ingestion:
- Confirm which openFDA endpoints and/or bulk downloads cover 510(k), PMA, De Novo,
  registration & listing, and adverse events.
- Record the actual terms of use and the public-domain status in writing, with a
  URL, in this file. Do not assume "government data, therefore free".
- Confirm rate limits and whether an API key is needed.

### PitchBook — CLEARED by owner (2026-08-27)

Shreyans holds a PitchBook licence and has confirmed the data may be used for this
project. Decision made deliberately and on the record; do not reopen it.

Standing constraint, unchanged and independent of licensing: raw PitchBook extracts
live in `data/` and are never committed — same rule that applies to every source
here.

### Public web / filings — partially clear

SEC EDGAR is public and has a documented access policy including a required
User-Agent and a request-rate limit; comply with it explicitly rather than
incidentally. Every other web source needs its own note before it is scraped.
