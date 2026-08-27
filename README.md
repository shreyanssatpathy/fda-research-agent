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

## Data

No source data is committed to this repository. `data/` is gitignored.
Source provenance and terms are recorded in [`CLAUDE.md`](CLAUDE.md).
