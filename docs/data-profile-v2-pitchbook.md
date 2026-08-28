# Phase 2 data profile — PitchBook

Files (not committed): `data/raw/pitchbook/`
Profiled 2026-08-27. Nothing has been ingested yet; this is the go/no-go read.

| file | shape | grain |
|---|---|---|
| `fda-pb-mapping.xlsx` | 1,483 × 2 | one row per FDA submission |
| `pitchbook_company_level.xlsx` | 1,483 × 168 | **denormalised to FDA row grain** |
| `pitchbook_deal_level.xlsx` | 920 × 116 | **one row per deal** |

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

## The blocking problem: a four-year temporal gap

**The deal export was downloaded 2021-12-31. The last deal is 2021-12-22. FDA data
runs to 2025-12-30.**

| | |
|---|---|
| Clearances after the last deal date | **933 of 1,367 (68.3%)** |
| Companies whose *first* clearance is after it | **255 of 473 (53.9%)** |

The plan's flagship Phase 2 questions — "which companies raised Series B before
their first FDA approval", "median capital raised before first clearance" — are
**unanswerable for the majority of the dataset**, and would be answered wrongly
rather than refused if this is not handled explicitly.

A company cleared in 2024 will look like it raised nothing after 2021. That is the
2026 coverage cliff again, but worse: it is *asymmetric* (one source is current,
the other is stale) and therefore invisible in any single result.

**This must be enforced in the contract, not documented and hoped for.** Either
refresh the PitchBook export, or scope Phase 2 questions to clearances on or
before 2021-12-22 and refuse the rest.

## Other findings

### Deal grain is clean — no fan-out

920 rows, 919 distinct `Deal ID`, zero duplicates. **One row per deal, not one row
per investor per round.** `SUM(Deal Size)` per company is therefore safe. This was
the single biggest risk going in and it is not present.

### Deal coverage is thin

Only **164 of 466 mapped FDA companies (35%)** have any deal. For the other 65%,
absence of funding data is not absence of funding — the same "missing is not zero"
rule that governs the 2026 boundary.

### Mixed currencies

`Native Currency of Deal`: USD 658, EUR 105, GBP 49, CAD 41, CNY 18, and others.
**Summing `Deal Size` across currencies without conversion is wrong.** Either
restrict to USD, or find the normalised column before any aggregate is allowed.

### `Deal Size` is 25% null

228 of 920. Undisclosed deals. A sum silently omits them, so any "total raised"
figure is a floor, not a total, and must say so.

### "Funding" needs defining

`Deal Type` spans Later Stage VC (171), Early Stage VC (153), **Grant (124)**,
Accelerator/Incubator (88), PIPE (66), Seed (59), Angel (40), M&A (36), Debt (34),
IPO (32). Summing all of them conflates an NIH grant, a debt facility, and an IPO
with venture funding. The contract must define which types count.

### The company file is denormalised

1,483 rows for 475 distinct Company IDs — it is the FDA rows joined to company
attributes, one row per clearance. **Must be deduplicated to one row per company on
load**, or every company-level aggregate is weighted by clearance count. 8 rows
have a null Company ID.

### Pre-modern deal dates

Earliest deal is Philips, 1912-01-01 (an IPO with a null size); also Edwards
Lifesciences 1966 and Medtronic 1973. These are legitimate for large incumbents but
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
