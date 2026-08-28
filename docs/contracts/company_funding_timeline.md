# Contract: `company_funding_timeline`

The cross-source table. One row per company, anchored on its **first FDA
clearance**, with venture funding split before and after.

- schema_version: `1.0.0`
- rows: **459 — one per company in `fda_510k`**
- built by `compose.materialize_timeline()` after both loaders run

## Why this table exists

FDA clearances and funding rounds are both many-per-company, so joining them
directly multiplies rows: Aidoc's 31 clearances times 7 rounds gives 217 rows and
a funding total **31x too large**. Valid SQL, confidently wrong number.

This table resolves that once, in code with tests around it, by collapsing FDA to
one row per company at first approval before joining deals. **You are querying the
answer, not the ingredients** — there is no join here to get wrong.

## Rules the generated SQL must follow

1. **Never join this table to `fda_510k` or `pb_deals`.** Neither is available to
   you, and re-joining would reintroduce exactly the fan-out this table exists to
   remove. Every column you need is here.
2. **`NULL` capital means unknown, not zero.** A company with `rounds_before = 0`
   has `capital_before_usd_m = NULL`. Never `coalesce(..., 0)` and never report it
   as "raised $0" — 120 of the 459 companies have no funding data at all.
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
| no funding data at all | 120 |
| outside the venture universe by design | GE Healthcare, Siemens, Philips, Medtronic |

Totals here are **floors**: undisclosed rounds were dropped at load, so a company
raised *at least* what this table says.

## What to refuse

- **Device, product-code or specialty questions** — not in this table.
- **Deal-type or investor questions** — not in this table.
- **"Which companies raised nothing"** — indistinguishable here from companies
  with no data. Report the 120 as unmeasured, not unfunded.
- **Anything implying these totals are complete.** See floors, above.
