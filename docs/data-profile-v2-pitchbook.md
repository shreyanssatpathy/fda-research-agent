# Phase 2 data profile — PitchBook

Files (not committed): `data/raw/pitchbook/`
Profiled 2026-08-27. Nothing has been ingested yet; this is the go/no-go read.

| file | shape | grain |
|---|---|---|
| `fda-pb-mapping.xlsx` | 1,483 × 2 | one row per FDA submission |
| `pitchbook_company_level.xlsx` | 1,483 × 168 | **denormalised to FDA row grain** |
| `pb_deals_latest.xlsx` | 3,385 × 184 | **one row per deal** |

## The bridge is good — better than the FDA name mapping was

`REGNUMBER → Company ID`, and it passes every integrity test:

- **It is a function.** Zero regnumbers map to more than one Company ID. (The
  `clean_name` mapping failed this test in Phase 0 — 16 applicants mapped to
  several names.)
- **99.4% coverage** of the V1 table: 1,359 of 1,367 clearances, 466 of 473
  companies.
- **No FDA company maps to several PitchBook IDs.**

### It also settles all three open company splits — with data, not judgment

The Phase 0 blocker in `docs/open-questions/company-mapping.md` is answerable:

| split | PitchBook IDs | verdict |
|---|---|---|
| `Iterative Scopes` / `Iterative Health` | both `231656-59` | **same company** (rename confirmed) |
| `Nemotec` / `Software Nemotec` | both `140027-05` | **same company** |
| `Samsung` / `Samsung Medison` | `59366-98` vs `51387-94` | **genuinely different filers** |

PitchBook's entity IDs are an independent source of truth for company identity,
and they resolve 15 FDA name pairs our precedence rule could not — including
`Ischema View`/`Ischemaview`, `CorVista Health`/`Corvista Health`,
`Fujifilm`/`Fujifilm Healthcare`, and `Bay Labs`/`Caption Health` (a rename).

**This inverts the plan's build order in a useful way.** Entity resolution was
scheduled after PitchBook integration; the bridge delivers a large part of it up
front.

## Temporal coverage is fine — superseded finding

An earlier export (`pitchbook_deal_level.xlsx`, downloaded 2021-12-31) ended at
2021-12-22 and left 68% of clearances outside the funding window. **It was the
wrong file and has been replaced.** Recorded because the check is the point, not
the outcome: an export's download date is a coverage boundary, and it has to be
read off the file rather than assumed.

The current export runs to **2026-08-07**, past the FDA extract's 2025-12-30.

| | |
|---|---|
| Clearances after the last deal date | **0** |
| FDA companies with at least one deal | **438 of 466 (94%)** |
| Companies with a deal *before* their first clearance | **398 of 449 (89%)** |

The plan's flagship Phase 2 questions — capital raised before first clearance, and
funding stage at time of clearance — are answerable for the large majority of the
dataset. Funding data is *ahead* of FDA data, which is the right direction: no
clearance sits outside the funding window.

The 28 mapped companies with no deals still need the "missing is not zero" rule —
absence of funding data is not evidence of no funding.

## Deal grain is clean — no fan-out

3,385 rows, 3,385 distinct `Deal ID`, zero duplicates. **One row per deal, not one
row per investor per round.** `SUM(Deal Size)` per company is therefore safe. This
was the single biggest risk going in and it is not present in either export.

### Mixed currencies

`Native Currency of Deal`: USD 2,254, EUR 395, GBP 176, KRW 108, CAD 101, CNY 86.
**Summing `Deal Size` across currencies without conversion is wrong.** Either
restrict to USD, or find the normalised column before any aggregate is allowed.

### `Deal Size` is 34% null — but the nulls are concentrated

1,143 of 3,385. Crucially they are not spread evenly:

| deal type | rows | % null size |
|---|---|---|
| Accelerator/Incubator | 588 | **82%** |
| Later Stage VC | 651 | 17% |
| Early Stage VC | 373 | 17% |
| Seed Round | 218 | 14% |
| Grant | 376 | 3% |

Excluding accelerator and incubator rows from "funding raised" both matches what
the question means and removes most of the missing-value problem at once. The
remaining ~17% on VC rounds are genuinely undisclosed, so any total is a **floor,
not a total**, and must say so.

### "Funding" needs defining

`Deal Type` spans Later Stage VC (651), Accelerator/Incubator (588), **Grant
(376)**, Early Stage VC (373), Seed (218), PIPE (174), Secondary (129), M&A (116),
IPO (91), Debt (82), PE Growth (74), Angel (73). Summing all of them conflates an
NIH grant, a secondary transaction, a debt facility and an IPO with venture
funding. The contract must define which types count as "raised".

### The company file is denormalised

1,483 rows for 475 distinct Company IDs — it is the FDA rows joined to company
attributes, one row per clearance. **Must be deduplicated to one row per company on
load**, or every company-level aggregate is weighted by clearance count. 8 rows
have a null Company ID.

### Pre-modern deal dates

Earliest deal is 1892. Large incumbents (Philips, Medtronic, Edwards) carry
IPO and M&A rows from long before the AI era. These are legitimate for large incumbents but
will distort any "time from founding to first raise" analysis. Not errors — just
not comparable to a 2019 seed round.

## Recommended sequence

1. **Resolve the three company splits using the bridge** — closes the Phase 0
   blocker, and improves V1 independently of Phase 2.
2. **Load company and deal tables**, deduplicating the company file, with the same
   validation-refuses-to-publish discipline as `fda_510k`.
3. **Write the PitchBook contract**, encoding: the 2021-12-22 boundary, the
   currency rule, the null-size rule, and the deal-type definition of "funding".
4. **Build and evaluate the PitchBook tool standalone** — its own eval cases, no
   joins.
5. **Only then compose**, joining in code over `company_id`.


---

## Cleaning rules applied (owner, 2026-08-27)

Implemented in `src/fda_agent/ingest_pitchbook.py`, enforced by tests.

| # | rule | rows after |
|---|---|---|
| — | raw | 3,385 |
| 1 | drop rows with no `Deal Size` | 2,242 |
| 2 | `Deal Status == 'Completed'` | 2,206 |
| 3 | `Universe` contains 'Pre-venture' or 'Venture Capital' | 2,038 |
| 4 | company's `Company Financing Status` in the qualified list | **1,964** |

**Rule 4 filters deals, not companies.** `pb_companies` keeps all 475 with an
`in_qualified_universe` flag, so the system can say "GE Healthcare is a public
corporation, outside the venture universe" rather than returning nothing and
leaving the reader to infer "no funding". 389 of 475 are in the qualified universe.

Rule 4 excludes the largest FDA filers by design — GE Healthcare (95 clearances),
Siemens (84), Philips (38), Canon Medical Systems (38), Medtronic (7) are all
`Corporation` or `Corporate Backed or Acquired`. **This is a stated scope, not a
gap:** cross-source answers cover the venture-backed universe, and the incumbents
that dominate FDA clearance counts have no funding profile by construction.

## Correction: `Deal Size` is already USD

An earlier note here flagged mixed currencies as needing conversion. **That was
wrong.** `Native Currency of Deal` labels the deal's original currency, but
`Deal Size` is normalised to USD millions. Evidence: 101 USD deals match their
stated amount exactly, and CNY deals convert at 6.77 and 6.89 — correct CNY/USD
rates.

## Open: "capital raised" is not yet defined, and the default is badly wrong

The four rules do not filter `Deal Type`, and the consequence is severe.

Querying capital raised before first clearance currently ranks **Apple first at
$297.6 billion**. Its 30 qualifying "rounds" are:

| deal type | n | USD m |
|---|---|---|
| Share Repurchase | 7 | 166,000 |
| General Corporate Purpose | 5 | 41,250 |
| Leveraged Recapitalization | 3 | 32,500 |
| Secondary Transaction – Open Market | 2 | 30,000 |
| Dividend Recapitalization | 4 | 24,500 |
| … | | |
| **Early Stage VC** | **3** | **4** |

A share repurchase is capital flowing *out* of the company to shareholders.
Counting it as "raised" is not an approximation, it is the wrong sign.

Across the whole cleaned table:

| scope | deals | USD m |
|---|---|---|
| all deal types | 1,964 | 501,811 |
| venture-style types only | 1,222 | 24,715 |

**95% of the capital comes from deal types that are not company fundraising.**

The distinction that matters is whether money reaches the company:

- **Primary** — Seed, Angel, Early/Later Stage VC, Accelerator/Incubator, PE
  Growth/Expansion. Money in.
- **Not fundraising** — Share Repurchase, Dividend/Leveraged Recapitalization,
  Secondary Transaction, Buyout/LBO, Merger/Acquisition. Money out, or between
  shareholders.
- **Arguable** — Grant (non-dilutive, real money in), IPO and PIPE (primary but
  public-market), Debt (money in, not equity).

This needs an owner ruling before the PitchBook contract is written.
