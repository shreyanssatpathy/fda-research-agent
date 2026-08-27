# Model comparison for SQL generation

Measured 2026-08-27 on the frozen v2 golden set, 38 cases, all fresh (no cache).
Same prompt, same contract, same scorer. Select with `FDA_MODEL`.

| model | score | cost / 38 questions | per question | answered something unanswerable |
|---|---|---|---|---|
| `claude-opus-5` | **33/38** | $0.3298 | $0.0087 | **none** |
| `claude-sonnet-5` | **33/38** | $0.1849 | $0.0049 | 1 — R02 |
| `claude-haiku-4-5` | 27/38 | $0.1644 | $0.0043 | 2 — R02, A02 |

## Read this with the noise in mind

Individual cases flip between runs. Opus scored 36/38 and then 33/38 on runs where
the only change was unrelated, with three cases changing verdict in both
directions. **A single run resolves to roughly ±3 cases**, so 33 vs 33 is a tie and
33 vs 27 is a real difference. Do not read a one-case delta as a model quality
signal; re-run before concluding anything.

## What actually separates them

Not SQL syntax — all three write valid DuckDB. The difference is **restraint**.

- **Haiku 4.5 fabricated twice.** It answered "what is the AI clearance trend so
  far this year?" (no 2026 data exists) and "how many devices has Canon cleared?"
  (Canon is an unresolved company split). Both are the failure mode this project
  is built to prevent — a confident number where the honest answer is "unknown"
  or "which Canon?".
- **Sonnet 5 fabricated once** (R02, the 2026 trend question).
- **Opus 5 declined all twelve** unanswerable and ambiguous cases.

Haiku also lost ground on multi-step reasoning: `first_clearance` 1/4 against
4/4 and 3/4 for the larger models.

## Recommendation

**Keep Opus 5 as the default.** It is the only model that declined every
unanswerable question, and refusal reliability is the product here — the SQL is
the easy part. At $0.0087 a question, a heavy day of 200 questions is under $2.

**Sonnet 5 is a reasonable production swap** at ~45% of the cost for the same
score, if you accept occasional over-answering and keep the refusal cases in CI to
catch drift. Its listed price is $3/$15 per MTok; introductory pricing of $2/$10
runs through 2026-08-31, so real cost is currently lower than the table shows
(`llm/budget.py` prices at the standard rate deliberately — a ceiling should
over-estimate, never under-estimate).

**Haiku 4.5 is dominated.** It costs about the same as Sonnet ($0.164 vs $0.185)
and scores six cases worse. There is no configuration where it is the right pick
here. It also rejects `output_config.effort` with a 400, so it needs a different
request shape — handled by `supports_effort()` in `text_to_sql.py`.

## Caching changes the economics

These figures are for fresh calls. Repeat questions are served from the
content-hash cache at zero cost, so steady-state spend depends on question
diversity, not volume. The model is part of the cache key, so switching models
never serves another model's answers.
