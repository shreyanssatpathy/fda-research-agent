# Contract: `fda_510k`

Authoritative description of the V1 table. This is the semantic layer the
text-to-SQL component is given — schema alone is not sufficient, because several
columns mean something other than their name suggests.

Amend with a dated note. Never diverge silently.

- schema_version: `1.0.0`
- grain: **one row per 510(k) clearance**, keyed by `regnumber`
- rows: 1367 | companies: 473 | coverage: 2010-05-12 to 2025-12-30

## What this table is

AI/ML-enabled medical devices cleared through the FDA 510(k) pathway, taken from
the FDA's published AI/ML-Enabled Medical Device List. It is **not** the full FDA
device corpus and **not** all submissions by these companies.

## Rules the generated SQL must follow

1. **Company filters use `company_name`, never `applicant_raw`.** `applicant_raw`
   is the literal filed string; 559 variants collapse into 485 companies. Filtering
   the raw field undercounts — 15 spellings of GE Healthcare exist.
2. **`regnumber` is the unique key.** Counting clearances is `count(*)` or
   `count(DISTINCT regnumber)`; they are equivalent here by construction.
3. **"First clearance" is `min(decision_date)` grouped by `company_name`**, and it
   means *first AI-enabled 510(k) clearance in this dataset*. It is not the
   company's first FDA clearance. Any answer must state the narrower claim.
4. **Use `decision_date` for "approved/cleared in <year>"**, not `date_received`.
   `date_received` is when FDA received the submission; the two differ by months
   and `year_received` disagrees with `decision_year` for many rows.
5. **2026 is outside the data.** The extract carries no 510(k) rows after
   2025-12-30. A 2026 question is out of range, never zero.
6. **Refuse rate, denial, and share-of-total questions.** Every row is a successful
   clearance (`decision_code` SESE/SESU) and there is no non-AI comparison group,
   so there is no denominator for "what fraction" or "how many were rejected".

## Columns

| column | type | meaning |
|---|---|---|
| `regnumber` | TEXT | FDA 510(k) number, e.g. `K213678`. Unique. Primary key. |
| `pathway` | TEXT | Always `510(k)` in V1. Retained so the filter is visible. |
| `applicant_raw` | TEXT | Company name exactly as filed. **Do not filter on this.** |
| `company_name` | TEXT | Normalized company. **The company dimension.** |
| `company_name_source` | TEXT | Winning source under `SOURCE_PRECEDENCE`. Provenance, not analysis. |
| `street`, `city`, `state`, `zip` | TEXT | Applicant address as filed. `state` is null for non-US filers. |
| `country` | TEXT | Two-letter code. US 626, IL 110, FR 88, CN 72, KR 68. |
| `committee_code` | TEXT | FDA advisory committee code. |
| `medical_specialty` | TEXT | Review panel. Radiology dominates (1071 of 1367). |
| `product_code` | TEXT | Three-letter FDA product code. **The reliable device-category dimension.** 164 distinct. |
| `submission_type` | TEXT | `Traditional` (1196), `Special` (160), `Abbreviated` (11). |
| `date_received` | DATE | Date FDA received the submission. |
| `decision_date` | DATE | Date FDA issued the decision. **Use this for time questions.** |
| `decision_code` | TEXT | `SESE` (1366) / `SESU` (1). Substantially equivalent. Near-constant. |
| `device_trade_name` | TEXT | Trade name on the submission, e.g. `a2z-Unified-Triage`. 1181 distinct. |
| `device_classification_name` | TEXT | FDA classification name for the product code. **34% null.** |
| `device_class` | TEXT | Risk class `1`/`2`/`3`, plus one `U`. 461 null. |
| `regulation_number` | TEXT | CFR reference, `<part>.<4-digit section>`, e.g. `892.2050`. 44% null. |
| `third_party` | BOOLEAN | Reviewed under the Third Party Review Program. True for 19 rows. |
| `year_received` | INTEGER | Year of `date_received`. |
| `decision_year` | INTEGER | Year of `decision_date`. |

## Column traps

### `device_trade_name` vs `device_classification_name`

These are `DEVICE NAME` and `DEVICENAME` in the source — one space apart, different
meanings. Trade name is the product's marketed name; classification name is the
official name of its product code. A device-name search means the trade name. A
"what kind of device" rollup means the classification name — but see below.

### `device_classification_name` is unreliable for rollups

34% null overall and worsening: 145 of 328 rows are null in 2025. Grouping by it
silently drops a third of recent clearances. **Group by `product_code` instead**,
which is never null.

### `medical_specialty` has a casing inconsistency

Six rows carry `pathology` lowercase while every other value is title-case. There
is no `Pathology`, so `WHERE medical_specialty = 'Pathology'` returns nothing.
Compare case-insensitively.

### `regulation_number` was recovered, not read

The source stores it as a float, losing trailing zeros (892.2050 arrives as
892.205). The loader zero-pads to four fractional digits, which every distinct
value round-trips through. Still 44% null.

## Known defects, not yet resolved

`company_name` is a function of `applicant_raw` for every applicant but three.
The `ming-mapping` precedence rule (owner ruling 2026-08-27) settled 13 of the 16
original conflicts; see
[`../open-questions/company-mapping.md`](../open-questions/company-mapping.md).

Still split, because the authoritative source contradicts itself:
`ITERATIVE SCOPES, INC.`, `SAMSUNG ELECTRONICS CO., LTD.`, `SOFTWARE NEMOTEC S.L.`
Company-level aggregates for those six rows are unreliable until ruled on.

Part B of that document — whether subsidiaries roll up to parents — is unaffected
by the precedence rule and remains open.
