# Evaluation

## `golden_v1.yaml` — 38 cases

**Status: FROZEN 2026-08-27 (`frozen: true`).**

Integrity is pinned in `golden_v1.sha256` and asserted by `tests/test_evals.py`.
Any edit to the file fails the suite — which is the point. Changing a frozen case
requires updating the hash deliberately and recording the ruling below.

The reference SQL is hand-written and is the thing under review. Every expected
answer was produced by executing that SQL against the built database, so no number
in the file was authored by hand — if the reference SQL misreads a question, the
expected answer is wrong in a visible way rather than a plausible one.

Once frozen, `frozen: true` is set and the file is never regenerated or edited to
improve a score (CLAUDE.md). A case found to be wrong is corrected only by an
explicit, dated ruling in this README.

### Coverage

| category | n | what it tests |
|---|---|---|
| `count` | 5 | simple filters and aggregates |
| `company` | 5 | company scoping via `company_name`, not `applicant_raw` |
| `first_clearance` | 4 | `min(decision_date)` grouped by company |
| `time_series` | 4 | annual counts, review-time deltas, both date columns |
| `device` | 6 | product-code rollups, trade-name search, null-heavy columns |
| `geography` | 3 | country and state filters |
| `refuse_out_of_range` | 2 | 2026 questions — must not answer zero |
| `refuse_no_denominator` | 3 | rates and shares with no baseline in the data |
| `refuse_not_in_data` | 3 | funding, recalls, PMA — out of V1 scope |
| `clarify` | 3 | ambiguous company references |

**Eight of the 38 cases must be refused and three must ask for clarification.** A
system that answers all 38 has failed, not passed. These are the cases that catch
confident fabrication, which is the failure mode the evidence layer exists to
prevent — and they are the reason the set is not just SQL correctness checks.

### Cases worth reviewing closely

- **F01–F04** turn on the narrow claim: "first AI-enabled 510(k) in this dataset",
  never "first FDA clearance". The SQL cannot express that caveat; the answer text
  must carry it.
- **D02** groups on `lower(medical_specialty)` because `pathology` is lowercase in
  the data while every other value is title-case.
- **D06** counts Class III devices where `device_class` is 34% null. The expected
  answer is 33, but a correct response should say the denominator is incomplete.
- **A01/A02** are ambiguous *because of known data defects* — Samsung and Canon are
  unresolved splits. If those get ruled on, these two cases change meaning and
  should be revisited deliberately.


## Amendments

None. The frozen file has not been edited.

## Known defects in the reference SQL (recorded 2026-08-27)

First full run: **29/38**. All 9 remaining failures were reviewed individually and
**all are defects in this set's reference SQL, not in the system under test.** They
are recorded rather than fixed, because editing a frozen set to raise a score is
exactly what the freeze exists to prevent.

The pattern is the same throughout: the reference SQL made a choice the question
did not imply and the contract did not specify, so the model had no way to predict
it. That is a defect in the question or the reference, not a wrong answer.

| id | what the reference does | why it is a defect |
|---|---|---|
| F04 | `LIMIT 3` on "which company was earliest" | Singular question; returning 1 is the better reading. The 3 is arbitrary. |
| G03 | `LIMIT 5` on "which state has the most" | Same. Singular question, arbitrary limit. |
| G01 | `LIMIT 10` on "which countries do companies file from" | An enumeration, not a ranking. All 34 is correct. |
| T03 | returns a third column (`n`) | Question asks about review time only. Violates contract rule 12. |
| T04 | returns `decision_year` alongside the count | "How many" is a scalar question. Violates contract rule 7. |
| D02 | `lower(medical_specialty)` in the SELECT | Violates contract rule 13 — return stored values, normalise only for matching. |
| D05 | picks `regnumber, company_name, device_trade_name` | Predates the default projection in rule 14. |
| D04 | resolves "grew fastest" as absolute change | Genuinely ambiguous — could be absolute or percentage. The system asked instead, which is better behaviour. |
| R02 | expects `refuse` for "trend so far this year" | System answered `clarify`. Both are defensible; scored partial. |

**Do not fix these by editing `golden_v1.yaml`.** The correct remedy is a `v2` set
authored against the contract's stated conventions, frozen separately, with `v1`
retained so the two are comparable. Until then, read 29/38 as the floor, and read
the per-category table as the signal:

- **refusals and clarifications: 9/9 + 1 partial.** Nothing was fabricated.
- **counts 5/5, company 5/5** — the categories with unambiguous expected shapes.
- The failing categories are the ones where the reference SQL improvised.

## Running

```
python evals/run.py            # sample mode: 8 cases
python evals/run.py --full     # all 38
```

Sample mode is the default per CLAUDE.md. The 8 sampled cases deliberately span
both answerable and unanswerable questions — a sample of only answerable ones
would hide exactly the failures that matter.

Generation requires an Anthropic API key (`ANTHROPIC_API_KEY`). Everything else —
the loader, the SQL guard, and the scorer — runs offline.

### How scoring works

- **Answerable case**: the model must choose `sql`; the SQL must survive the
  guard, execute, and produce the same row count and the same values in the same
  order. Column *names* are ignored (`n` vs `total` is style).
- **Unanswerable case**: the model must choose `refuse` (or `clarify` for the
  ambiguous ones). Producing SQL scores as a **failure**, not partial credit —
  answering an unanswerable question confidently is the failure mode this project
  exists to prevent.
- Declining an answerable question is also a failure. Refusing everything scores
  no better than answering everything.

`tests/test_eval_runner.py` asserts that every reference query scores as a pass
against its own frozen answer. If the scorer cannot recognise the known-correct
SQL as correct, no score it produces means anything.


---

# `golden_v2.yaml` — 38 cases

**Status: FROZEN 2026-08-27.** Supersedes v1. v1 is retained unmodified so the two
sets stay comparable; `tests/test_evals.py` asserts both hashes and asserts that
every v2 question is byte-identical to its v1 counterpart.

## What changed

Nine reference queries, each corrected against a **stated contract rule**, with
the rule recorded on the case in `changed_from_v1`. No question was reworded — only
reference SQL and expected actions changed. A correction without a recorded
justification is indistinguishable from tuning the set to fit the model, so the
test suite requires one on every changed case.

Seven were corrected SQL (F04, G03, G01, T03, T04, D02, D05). Two were
reclassified because there is no correct SQL:

- **D04** — "which product code grew fastest" is ambiguous between absolute and
  percentage change. v1 silently resolved it as absolute; v2 expects `clarify`.
- **R02** — refusing and clarifying are both defensible, so v2 introduces a
  `decline` expectation that accepts either.

v2 also states the expected action per case rather than inferring it from the
category, which is what makes `decline` expressible.

## Result: 36/38

| category | v1 | v2 |
|---|---|---|
| count | 5/5 | 5/5 |
| company | 5/5 | 5/5 |
| first_clearance | 3/4 | 4/4 |
| device | 3/6 | 6/6 |
| time_series | 2/4 | 3/4 |
| geography | 1/3 | 2/3 |
| refuse / clarify | 9+1/12 | 12/12 |

The jump from 29 to 36 is **not** an improvement in the system — the system did not
change between those runs. It is the correction of defects in v1's reference SQL.
Both numbers are real; they measure different things, which is why v1 is kept.

## The two remaining failures are honest ambiguity

Neither is fixable by a contract rule, because both turn on what the question
means, not on how to express it.

- **T03** — "has review time gotten longer?" The reference uses the median; the
  system used the mean. Both are defensible summaries of review time, and the
  question does not say which. They agree on 2010 and diverge from 2012.
- **G01** — "which countries do AI device *companies* file from?" The reference
  counts clearances (US = 626); the system counted distinct companies (US = 222).
  The question says "companies", so the system's reading is arguably the better
  one and the reference is arguably wrong.

These are recorded, not fixed. Fixing them means rewording the questions, which
would make v2 incomparable to v1 — that belongs in a v3, if ever.


## v2 partially superseded by a contract change (2026-08-27)

**M01 and D05 now fail by design.** Owner decision: a clearance listing returns
*every* column, because a researcher wants the whole record and will narrow it
themselves. Contract rule 14 changed from a four-column default projection to
`SELECT *`.

Both cases froze the old four-column expectation, so their expected answers no
longer describe correct behaviour. They are **left failing rather than edited** —
v2 is frozen, and the honest reading is "two cases are stale", not "the system
regressed".

Fixing this means a v3 with those two references regenerated. Until then, read the
v2 ceiling as 36/38, not 38/38.

## Run-to-run variance

Repeat runs of the same model on the same set differ by roughly **±3 cases**.
Observed: Opus 5 scored 36/38 and 33/38 on runs whose only difference was
unrelated, with cases flipping in both directions — F02 and F04 (whether a first-
clearance answer includes the date column) and G01 are the unstable ones.

Treat a single run as an estimate. A one- or two-case delta is not a signal; re-run
before concluding a change helped.


---

# `golden_v3.yaml` — 38 cases

**Status: FROZEN 2026-08-27.** Supersedes v2. v1 and v2 retained unmodified; all
three hashes are asserted, and a test checks every v3 question is still
byte-identical to its v1 original.

## What changed from v2 (3 cases)

| id | change | reason |
|---|---|---|
| M01 | 4 columns → all 24 | Rule 14 changed to `SELECT *` (owner decision). v2 froze the superseded projection. |
| D05 | 4 columns → all 24 | Same. |
| G01 | counts clearances → counts distinct companies | The question's subject is companies; the v2 reference measured clearances. |

A contract rule was also added and one extended:

- **Rule 15** — summarise a distribution with the **median** unless the question
  names a statistic. Review times are right-skewed, so the mean overstates what a
  typical submission experiences. (T02 says "on average" and correctly stays `avg`.)
- **Rule 7 extended** — a **superlative question returns the entity and the
  measure**, not the entity alone. "Which company was earliest?" returns the name
  *and the date*. This aligned F04 with G03, M02 and D01, which already did this.

## Result: 37/38

| category | v1 | v2 | v3 |
|---|---|---|---|
| count | 5/5 | 5/5 | 5/5 |
| company | 5/5 | 5/5 | 5/5 |
| first_clearance | 3/4 | 4/4 | 4/4 |
| device | 3/6 | 6/6 | 6/6 |
| time_series | 2/4 | 3/4 | 4/4 |
| geography | 1/3 | 2/3 | 2/3 |
| refuse / clarify | 9+1/12 | 12/12 | 12/12 |

## G01: I corrected toward noise, and the next run proved it

Worth recording as a caution against the exact discipline failure this process is
designed to prevent.

The v3 build script flagged, in advance, that correcting G01 "also happens to make
a failing case pass" and that a reader might disagree. **The very next run took the
opposite reading** — the model counted clearances (626) where the run I corrected
against counted companies (222).

So the correction was fitted to a single sample of an unstable case, not to a
defect. The reference is now arguably no better than it was; the honest conclusion
is that **the question is genuinely ambiguous** and neither reading is wrong.

The v3 reference is left as-is rather than reverted — flipping it back would be
chasing the same noise in the other direction. What changes is the reading of the
score: **37/38 with the failing case rotating between F04 and G01 by run.** Treat
the result as 37±1.

*Lesson: flagging a fitting risk is not the same as avoiding it. On an unstable
case, one run is not evidence — re-run before correcting, or leave it alone.*


---

# `golden_pitchbook.yaml` — 23 cases

**Status: FROZEN 2026-08-27.** Standalone: no cross-source questions, so a failure
is attributable to one layer. 15 answerable, 6 refuse, 2 clarify.

## Result: 18/23

| category | score |
|---|---|
| refuse_needs_fda | **3/3** |
| refuse_not_in_data | **3/3** |
| clarify | **2/2** |
| capital | 3/4 |
| deals | 3/4 |
| companies | 2/3 |
| count | 2/4 |

**All 8 decline cases pass on the first run.** The tool refuses questions needing
FDA data, refuses investor and revenue questions, refuses funding for companies
outside the venture universe, and asks which Samsung is meant.

## What the first run found

### A real bug in the SQL guard

`date_part('year', deal_date)` was **blocked as an unrecognised function**. It is
valid, harmless DuckDB, and it was treated as an exfiltration attempt.

The claim in `sql-safety.md` — that rejecting every unrecognised function "costs
nothing legitimate" — was verified against the FDA golden set, which never used
one. It was true of that sample, not of SQL in general. Fixed with an explicit
`FUNCTION_ALLOWLIST` of safe scalar and aggregate functions; every filesystem
reader is still rejected, with tests asserting both halves and a test forbidding
`read_*` from ever entering the allowlist.

### A contract I wrote without the lessons of the previous one

The first run scored **9/23**. Every answerable failure was extra columns or a
missing `LIMIT` — the values were all correct. The FDA contract earned its
result-shape rules over five iterations; I wrote the PitchBook contract from
scratch and never ported them. Adding rules 7, 8, 9, 11, 12, 13 and 15 took it to
17/23 in one pass.

*Lesson: contract conventions are source-agnostic and should be shared, not
rediscovered per source.*

## Known defects in this set's reference SQL

Recorded, not fixed — the set is frozen.

| id | issue |
|---|---|
| P01 | "How many funding deals" — the reference counts all 1,988 deals including share repurchases. The system counted the 1,240 venture rounds, which is the better reading of "funding". |
| P03 | "How many companies have funding data" is ambiguous between any deal and any venture round; the system asked, which is defensible. |
| P05 | The reference rounds to 2 decimals (`420.26`), violating the contract's own rule 11. The system returned `420.3`, correctly. |
| P12 | A listing convention is missing — PitchBook has no equivalent of FDA rule 14, so column choice for "show all rounds" is unspecified. |
| P13 | "Which countries…" is an enumeration, not a ranking; the reference caps at 10. Same ambiguity as G01 in the FDA set. |

Three of the five are cases where **the system was more right than the reference**.


---

# `golden_pitchbook_v2.yaml` — 23 cases

**Status: FROZEN 2026-08-27.** Supersedes `golden_pitchbook.yaml`, which stays
frozen. Questions byte-identical; only references changed.

## Result: 23/23

| category | v1 | v2 |
|---|---|---|
| capital | 3/4 | **4/4** |
| count | 2/4 | **4/4** |
| deals | 3/4 | **4/4** |
| companies | 2/3 | **3/3** |
| refuse / clarify | 8/8 | **8/8** |

**18/23 → 23/23 with no change to the system.** Three of the five corrections were
cases where the model was already more correct than the reference:

| id | correction |
|---|---|
| P01 | The contract defines "funding" as venture rounds; the v1 reference counted all 1,988 deals including share repurchases. **The model's 1,240 was right.** |
| P03 | Same definition — "companies with funding data" means companies with a venture round (332), not any deal (359). |
| P05 | Rule 11 requires one decimal. The v1 reference rounded to two (`420.26`), violating the contract it was written against. **The model's `420.3` was right.** |
| P12 | Unchanged SQL; the contract gained a default listing projection (new rule 14). v1 left column choice unspecified. |
| P13 | An enumeration, not a ranking, so rule 8's top-10 default does not apply. Same defect as G01 in the FDA set. |

Two contract changes made these well-defined rather than merely agreed-with: a
listing projection rule, and a sharpened definition of "funding" that tells the
model to answer rather than ask.

**Read 23/23 carefully.** It measures agreement with a reference set that has now
been corrected twice against the same contract the model reads. It is a real
result, but a set this small and this closely coupled to the contract cannot
support a claim of general reliability.
