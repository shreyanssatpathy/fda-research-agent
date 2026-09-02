# Contract: `company_funding_timeline` and `v_company_deals`

The cross-source objects. Both anchored on each company's **first FDA clearance**,
covering venture rounds only.

- schema_version: `2.0.0`
- `company_funding_timeline` — **459 rows, one per company**
- `v_company_deals` — **1,159 rows, one per venture round**
- both are **SQL views**, defined by `compose.create_views()`

## Two grains — pick the one the question needs

| object | grain | use for |
|---|---|---|
| `company_funding_timeline` | one company | anything counted or averaged *across companies* — medians, cohorts, "how many companies…" |
| `v_company_deals` | one venture round | anything about individual rounds — sizes, types, timing between a round and first clearance |

Joining them on `company_name` is permitted and safe (many rounds to one company).

### Why views, not a materialised table (changed 2026-09-02)

The previous version was a table that pre-aggregated deals into before/after
buckets and **discarded every deal date**. "Average time from first funding to
first approval" was therefore unanswerable — the dates existed in the inputs and
did not survive the aggregation.

Fixing the *grain* is what prevents fan-out; pre-aggregating the *measures* was a
bet on which questions would be asked, and it lost. Views fix the grain without
the bet, cannot go stale, and need no rebuild step. At 459 companies the
recomputation is immaterial.

### The counting rule for `v_company_deals`

Company attributes (`first_clearance`, `total_clearances`) are denormalised onto
**every one of that company's deal rows**. Aidoc has 31 clearances and 7 rounds,
so `total_clearances = 31` appears 7 times.

- `sum(total_clearances)` returns **217**. Wrong.
- `any_value(total_clearances)` returns **31**. Right.
- `count(*)` counts **deals** — not companies, not clearances.
- `sum(deal_size_usd_m)` is correct: each deal appears exactly once.

**Deal columns are safe to aggregate. Company columns are not.** For any
company-level count or average, use `company_funding_timeline` instead.

### Why 1,159 and not 1,240

`pb_deals` holds 1,240 venture rounds. The 81 excluded belong to PitchBook
companies with no FDA clearance — there is no first approval to anchor them to.

## Why this table exists

FDA clearances and funding rounds are both many-per-company, so joining them
directly multiplies rows: Aidoc's 31 clearances times 7 rounds gives 217 rows and
a funding total **31x too large**. Valid SQL, confidently wrong number.

This table resolves that once, in code with tests around it, by collapsing FDA to
one row per company at first approval before joining deals. **You are querying the
answer, not the ingredients** — there is no join here to get wrong.

## Rules the generated SQL must follow

1. **Never join these views to `fda_510k` or `pb_deals`.** Neither is available
   to you, and re-joining would reintroduce exactly the fan-out these views exist
   to remove. Joining the two views to each other on `company_name` is fine.
2. **`NULL` capital means unknown, not zero.** A company with `rounds_before = 0`
   has `capital_before_usd_m = NULL`. Never `coalesce(..., 0)` and never report it
   as "raised $0" — **147 of the 459 companies have no venture round here at
   all**, and a further 1 has only an undated one, so 148 have no
   `first_funding_date`. (An earlier version of this contract said 120. That
   figure counted companies with no deal of *any* type; these views carry venture
   rounds only, so the correct number is higher. Corrected 2026-09-02.)
   `avg()` and `median()` already skip NULLs, which is correct; `sum()` treating
   them as zero is fine, but the *count* of contributing companies must be stated.
3. **Say how many companies a statistic covers.** "Median capital raised before
   first clearance" is a statistic about the 289 companies with funding data, not
   about all 459. State the denominator.
4. **Undated rounds are in neither before nor after, and any capital aggregate
   must say how many are affected.** `undated_venture_rounds` counts venture rounds with
   no date. They are excluded from both buckets, because a deal that cannot be
   placed in time cannot be called "before" or "after".

   This is not a footnote: **25 companies hold 26 undated venture rounds worth
   $211.1m that no before/after figure includes.** (PitchBook holds 29 undated
   venture rounds in total; 26 belong to companies in the FDA data.)

   **The column counts venture rounds only, which is what its name says.** 14
   further deals have no date but are not venture rounds — 9 grants, plus debt,
   PIPE, capitalization and secondary rows. Those are excluded from every capital
   figure on **deal-type** grounds (rule 1), not date grounds, so their missing
   dates change nothing. Fitbit is the clearest example: one undated $0.1m grant,
   `undated_venture_rounds = 0`, and no capital figure affected either way.

   The consequence to state honestly: **37 FDA companies have an undated deal of
   some kind; 25 of them show it here.** If a question is about data completeness
   rather than capital, this column is the wrong instrument and the answer should
   say so. Their `capital_before_usd_m` is
   therefore understated, and a median or mean over that column inherits the
   understatement silently.

   So any query aggregating `capital_before_usd_m` or `capital_after_usd_m`
   **must** also select `sum(undated_venture_rounds)` or
   `count(*) FILTER (WHERE undated_venture_rounds > 0)`, and the answer must state it.
   Reporting a median of pre-clearance capital without noting how many companies
   have unplaceable rounds presents an understated figure as a complete one.
5. **This table has no device, product-code or deal-type detail.** Questions about
   what a company cleared, or which deal types it raised, need the FDA or
   PitchBook source instead — refuse rather than approximate.

## Columns

| column | type | meaning |
|---|---|---|
| `company_name` | TEXT | FDA canonical name. Unique. Primary key. |
| `pb_company_id` | TEXT | PitchBook identifier. **NULL for 7 companies** not in the bridge. |
| `first_clearance` | DATE | Earliest 510(k) decision date. Never null. |
| `total_clearances` | BIGINT | All AI 510(k) clearances, not just the first. |
| `rounds_before` | BIGINT | Dated venture rounds strictly before `first_clearance`. 0, never null. |
| `capital_before_usd_m` | DOUBLE | Their sum, USD millions. **NULL when `rounds_before = 0`.** |
| `rounds_after` | BIGINT | Dated venture rounds on or after `first_clearance`. |
| `capital_after_usd_m` | DOUBLE | Their sum. **NULL when `rounds_after = 0`.** |
| `undated_venture_rounds` | BIGINT | Venture rounds with no date. In neither bucket. |
| `first_funding_date` | DATE | Earliest dated venture round. NULL when none. |
| `last_funding_date` | DATE | Latest dated venture round. NULL when none. |
| `days_first_funding_to_first_clearance` | BIGINT | Days from first round to first clearance. **Negative** when the first round came after clearance. NULL when either date is missing. |

## `v_company_deals` columns

| column | type | meaning |
|---|---|---|
| `deal_id` | TEXT | PitchBook deal identifier. Unique. |
| `company_name` | TEXT | FDA canonical name. Repeats per round. |
| `pb_company_id` | TEXT | PitchBook identifier. Repeats per round. |
| `deal_date` | DATE | Date of the round. **26 rows are NULL.** |
| `deal_type` | TEXT | Seed, Angel, Early/Later Stage VC, Accelerator/Incubator, PE Growth/Expansion, Equity Crowdfunding. |
| `deal_size_usd_m` | DOUBLE | USD millions. Never null. Safe to sum. |
| `deal_size_status` | TEXT | `Actual` or `Estimated`. |
| `first_clearance` | DATE | The company's first clearance. **Company attribute — do not sum.** |
| `total_clearances` | BIGINT | The company's clearance count. **Company attribute — do not sum.** |
| `days_to_first_clearance` | BIGINT | Days from this round to first clearance. Negative for post-clearance rounds. NULL when undated. |
| `is_before_first_clearance` | BOOLEAN | NULL when the round is undated. |

"Venture rounds" means `is_venture_round` in `pb_deals`: Seed, Angel, Early/Later
Stage VC, Accelerator/Incubator, PE Growth/Expansion, Equity Crowdfunding. Share
repurchases, IPOs, M&A and debt are excluded — see
[`pitchbook.md`](pitchbook.md) rule 1.

## Result shape

Rules 7, 8, 9, 11, 12, 13 and 15 from [`fda_510k.md`](fda_510k.md) apply
identically: one number for a scalar question, top 10 for an unqualified ranking,
deterministic tie-breaks, one decimal on non-integer aggregates, median by default
for distributions.

## Coverage

| | |
|---|---|
| companies | 459 |
| with funding before first clearance | **289** |
| with any venture funding recorded | 332 |
| no venture round at all | 147 |
| no `first_funding_date` (adds one with only an undated round) | 148 |
| not in the FDA-to-PitchBook bridge | 7 |
| outside the venture universe by design | GE Healthcare, Siemens, Philips, Medtronic |

Totals here are **floors**: undisclosed rounds were dropped at load, so a company
raised *at least* what this table says.

## What to refuse

- **Device, product-code or specialty questions** — not in this table.
- **Deal-type or investor questions** — not in this table.
- **"Which companies raised nothing"** — indistinguishable here from companies
  with no data. Report the 147 as unmeasured, not unfunded.
- **Anything implying these totals are complete.** See floors, above.
