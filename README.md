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
pip install -r requirements.txt
python -m fda_agent.ingest        # build the database from data/raw/fda/
pytest                            # 117 tests, all offline
```

SQL generation needs an Anthropic API key. Copy `.env.example` to `.env` (which is
gitignored) and fill it in, or export `ANTHROPIC_API_KEY` in your shell. The
loader, the SQL guard, and eval scoring all run without one.

```
python evals/run.py               # sample: 8 of 38 cases
python evals/run.py --full        # all 38
```

## Data

No source data is committed to this repository. `data/` is gitignored.
Source provenance and terms are recorded in [`CLAUDE.md`](CLAUDE.md).
