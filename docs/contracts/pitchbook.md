# Contract: `pb_deals`, `pb_companies`, `fda_pb_bridge`

Authoritative description of the Phase 2 tables. Same role as
[`fda_510k.md`](fda_510k.md): the semantic layer the text-to-SQL component is
given, because schema alone is not sufficient.

Amend with a dated note. Never diverge silently.

- schema_version: `1.0.0`
- source: PitchBook exports supplied by the owner, licence held (see `CLAUDE.md`)
- deal coverage: 1977-01-01 to **2026-08-07** — ahead of the FDA extract's 2025-12-30

## Grain — the assumption every aggregate rests on

| table | grain | rows |
|---|---|---|
| `pb_deals` | **one row per deal** | 1,988 |
| `pb_companies` | **one row per company** | 475 |
| `fda_pb_bridge` | **one row per FDA submission** | 1,409 |

`deal_id` is unique across all 1,988 rows. The export is *not* one row per
investor per round, so `sum(deal_size_usd_m)` per company does not fan out. The
company export arrived denormalised to FDA clearance grain (1,483 rows for 475
companies) and is deduplicated on load — without that, every company-level
aggregate would be weighted by how many devices the company cleared.

## Rules the generated SQL must follow

1. **"Capital raised" means `is_venture_round = true`. Always.** Never
   `sum(deal_size_usd_m)` over all deal types. The table contains Share
   Repurchases ($168.6bn), Mergers/Acquisitions, and Leveraged Recapitalizations —
   capital flowing *out* of the company or between shareholders. Including them is
   not an approximation, it is the wrong sign. Unfiltered, Apple ranks first for
   "capital raised before first FDA clearance" at $297.6bn, of which $4m is actual
   venture funding.

2. **Every total is a floor, not a total.** Rule 1 of the load drops deals with no
   disclosed size, so undisclosed rounds are absent entirely — 1,143 of 3,385 raw
   deals. Say "at least $X across N disclosed rounds", never "$X raised".

3. **`deal_size_usd_m` is USD millions and already converted.** `native_currency`
   labels the deal's original currency; it is not a conversion instruction. Do not
   filter or convert on it.

4. **Absence of deals is not absence of funding.** 339 of the 459 FDA companies
   have deals here. For the other 120, the honest answer is "no funding data in
   this dataset", never "$0" and never "unfunded".

5. **The four largest FDA filers are outside this data by design.** GE Healthcare
   (95 clearances), Siemens (84), Philips (38) and Medtronic (7) are
   `Corporation` — not venture-backed, so they have no funding profile here. A
   question about their funding must say they are out of scope, not that they
   raised nothing.

6. **Join FDA to PitchBook only through `fda_pb_bridge`.** Never on company name.
   The bridge maps `regnumber → company_id` and is a function; 8 of the 1,367
   clearances are unmapped.

## `pb_deals`

| column | type | meaning |
|---|---|---|
| `deal_id` | TEXT | PitchBook deal identifier. Unique. Primary key. |
| `company_id` | TEXT | Joins to `pb_companies` and `fda_pb_bridge`. 359 distinct. |
| `company_name_pb` | TEXT | PitchBook's company name. **Not** the FDA `company_name` — see the naming note below. |
| `deal_date` | DATE | Date of the deal. **43 nulls** — those deals cannot be placed in time and must be excluded from before/after comparisons. |
| `deal_size_usd_m` | DOUBLE | Deal size, USD millions. Never null (load rule 1). |
| `deal_size_status` | TEXT | `Actual` (1,795) or `Estimated` (141), 52 null. Estimated figures are PitchBook's inference, not disclosure. |
| `deal_type` | TEXT | 34 values. See the venture list below. |
| `deal_class` | TEXT | Coarser grouping: Venture Capital (1,018), Other (454), Debt (146), Corporate (132)… **Do not use this to define funding — use `is_venture_round`.** |
| `deal_status` | TEXT | Always `Completed` (load rule 2). Carries no information; do not filter on it. |
| `universe` | TEXT | Always contains 'Pre-venture' or 'Venture Capital' (load rule 3). |
| `native_currency` | TEXT | Label only. See rule 3 above. |
| `pre_money_valuation_usd_m` | DOUBLE | 63% null. |
| `post_valuation_usd_m` | DOUBLE | 59% null. |
| `is_venture_round` | BOOLEAN | **The funding filter.** 1,240 true / 748 false. |

### `is_venture_round`

True for: `Seed Round`, `Angel (individual)`, `Early Stage VC`, `Later Stage VC`,
`Accelerator/Incubator`, `PE Growth/Expansion`, `Equity Crowdfunding`.

These are primary capital into the company. Everything else — IPO, PIPE, Grant,
Debt, M&A, Buyout, Secondary, Share Repurchase, Recapitalization — is either not
fundraising or not equity. Those rows are **retained deliberately**: an
acquisition or IPO is evidence for a company's corporate history, it just is not
"capital raised".

| | deals | USD m |
|---|---|---|
| `is_venture_round = true` | 1,240 | 24,799.9 |
| `is_venture_round = false` | 748 | 477,487.3 |

The second row is why rule 1 exists.

## `pb_companies`

| column | type | meaning |
|---|---|---|
| `company_id` | TEXT | Unique. Primary key. |
| `company_name_pb` | TEXT | PitchBook's current name for the company. |
| `company_legal_name` | TEXT | Registered legal name. 9 null. |
| `company_former_name` | TEXT | Previous name(s). 353 null. Useful for resolving renames. |
| `financing_status` | TEXT | 12 values; drives `in_qualified_universe`. |
| `business_status` | TEXT | e.g. Generating Revenue, Profitable. |
| `ownership_status` | TEXT | Privately Held, Publicly Held, Acquired/Merged… |
| `year_founded` | DOUBLE | 10 null. |
| `total_raised_usd_m` | DOUBLE | PitchBook's own lifetime total, 95 null. **Not** the sum of `pb_deals` — different scope and vintage. Do not mix the two in one answer; say which you used. |
| `hq_country`, `hq_city` | TEXT | Headquarters. |
| `website` | TEXT | 3 null. |
| `universe` | TEXT | Comma-joined labels. |
| `in_qualified_universe` | BOOLEAN | 411 true / 64 false. |

### `in_qualified_universe`

True for the eight owner-approved financing statuses plus `Corporate Backed or
Acquired`. False for `Corporation` (62 companies) and two `Failed Transaction`
rows.

**Companies are kept when false, not dropped.** That is what lets the system say
"GE Healthcare is a public corporation, outside the venture universe" instead of
returning nothing and letting the reader infer "no funding". Deals for those
companies are excluded, so `in_qualified_universe = false` always implies zero
deals — that is scope, not evidence.

## `fda_pb_bridge`

| column | type | meaning |
|---|---|---|
| `regnumber` | TEXT | FDA submission number. Unique — the bridge is a function. |
| `company_id` | TEXT | PitchBook company. 475 distinct. |

`fda_510k.pb_company_id` carries the same value, already joined, so a
single-source FDA question does not need this table.

## Column traps

### `company_name_pb` is not `fda_510k.company_name`

PitchBook's current name and the FDA filing name differ often: `RapidAI` versus
`Ischema View`, `Caption Care` versus `Bay Labs`, `EarliPoint Health` versus
`EarliTec Diagnostics`. **Company questions are answered against the FDA name**
(`fda_510k.company_name`, contract rule 10 there); `company_name_pb` is for
display and for resolving renames.

### `total_raised_usd_m` and `sum(deal_size_usd_m)` disagree

The first is PitchBook's lifetime figure for the company; the second is the sum of
deals surviving this project's load rules. They measure different things and will
not reconcile. Pick one, and name it in the explanation.

### `deal_date` is null on 43 rows

Those deals exist but cannot be ordered in time. Any "before first clearance" or
"funding by year" question must exclude them, and should say how many it dropped.

### Pre-modern deals

The earliest deal is 1977, and large incumbents carry IPO and M&A rows from
decades before the AI era. Fine for corporate history; not comparable to a 2019
seed round.

## What to refuse

- **Funding for a company outside the venture universe** — GE, Siemens, Philips,
  Medtronic. Out of scope, not zero.
- **Funding for a company with no deals here** — 120 of 459 FDA companies. No data,
  not unfunded.
- **Investor-level questions.** Investor names are not in these tables. "Which
  investors backed the most AI device companies" is unanswerable.
- **Valuation questions for most companies** — 59-63% null.
- **Anything implying these totals are complete.** Undisclosed rounds were dropped
  at load; see rule 2.
