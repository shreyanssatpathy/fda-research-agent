# fda-research-agent

**Agentic MedTech Research System** — an AI research copilot that combines
natural-language querying, text-to-SQL, entity resolution, structured FDA and
investment datasets, and public-source research to answer MedTech company and
market questions with traceable evidence.

## Status

Phase 0. Plan recorded, source terms not yet verified, no code written.

## Documents

- [`docs/PLAN.md`](docs/PLAN.md) — full phase-by-phase architecture and roadmap
- [`CLAUDE.md`](CLAUDE.md) — project invariants and source-licensing status

## Design principle

> The evidence layer determines facts. The LLM writes the narrative.

Every fact carries its source. Missing data is reported as missing. Conflicting
sources are surfaced as conflicts.

## Setup

```
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
PYTHONPATH=src ./.venv/bin/python -m fda_agent.ingest    # build the database
PYTHONPATH=src ./.venv/bin/python -m pytest              # 117 tests, all offline
```

Use a virtualenv rather than a base conda environment: `anthropic` 1.x requires
`httpx2`, and a mixed `httpx`/`httpcore` stack fails at request time with a
misleading "Connection error".

SQL generation needs an Anthropic API key. Copy `.env.example` to `.env` (which is
gitignored) and fill it in, or export `ANTHROPIC_API_KEY` in your shell. The
loader, the SQL guard, and eval scoring all run without one.

```
PYTHONPATH=src ./.venv/bin/python evals/run.py           # sample: 8 of 38
PYTHONPATH=src ./.venv/bin/python evals/run.py --full    # all 38
```

Current score: **29/38**, first full run. All refusal and clarification cases pass
— the system declines what the data cannot answer instead of fabricating. The
remaining failures are documented defects in the eval set's own reference SQL; see
`evals/README.md`.

## Interface

```
PYTHONPATH=src ./.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1
```

`research(question)` routes each question to one source — FDA clearances, company
funding, the cross-source funding-vs-clearance timeline, or a deterministic
company profile — and the interface shows which route was chosen and why.

It renders **refusals, clarifications, empty results, and blocked
queries as four visibly different things**. That distinction is the point: a
refusal and a zero-row table look identical if both are just an empty dataframe,
and conflating them is how a user reads "no clearances in 2026" off a system that
simply has no 2026 data.

Every answer carries its generated SQL, the safety layer's rewritten SQL, and the
model, prompt version, and contract hash that produced it.

## Data

No source data is committed to this repository. `data/` is gitignored.
Source provenance and terms are recorded in [`CLAUDE.md`](CLAUDE.md).
