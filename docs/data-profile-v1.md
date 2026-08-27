# V1 data profile — FDA AI/ML device list

Source file: `data/raw/fda/FDA_AI_list21Jul2026.xlsx` (not committed)
Provenance: FDA AI/ML-Enabled Medical Device List, extract dated 21 Jul 2026,
with a `clean_name` company mapping added by the project owner.
Profiled: 2026-08-27

## Shape

1483 rows x 31 columns, one sheet. `AI_ML_submission` is 1 for every row, so the
file is entirely AI/ML submissions — there is no non-AI comparison group.

By pathway:

| PATHWAY | rows |
|---|---|
| 510(k) | 1367 |
| PMA | 79 |
| De Novo | 37 |

**V1 works the 1367 510(k) rows.**

## Why the 510(k) subset is the right grain

`REGNUMBER` is unique across all 1367 510(k) rows — one row per clearance, no
supplements, no fan-out. Every duplicate `REGNUMBER` in the file (66 of them) is
a PMA supplement: one PMA carries many `SUPPLEMENT NUMBER` rows over time, so any
count over the full file double-counts. Excluding PMA removes that entirely.

`SUPPLEMENTTYPE`, `SUPPLEMENT NUMBER`, `SUPPLEMENTREASON`, `DOCKETNUMBER`,
`FEDREGNOTICEDATE`, `REVIEWGRANTEDYN` are effectively all-null within 510(k) and
are dropped from the V1 model.

## Coverage boundaries

- **510(k) data stops at 2025-12-30.** There are zero 510(k) rows in 2026 despite
  the July 2026 file date. A question about 2026 must return "outside the data
  range", never "0 clearances".
- **Only successful clearances are present.** `DECISION CODE` is SESE for 1366
  rows and SESU for 1 — substantially equivalent. There are no NSE decisions, so
  clearance *rates*, denials, and withdrawal questions are unanswerable.
- **No non-AI baseline**, so share-of-total questions are unanswerable.

## Column traps

These are the ones that will silently produce wrong SQL if the model sees only
raw column names.

### `DEVICE NAME` vs `DEVICENAME` — different meanings, one space apart

- `DEVICE NAME` (1181 distinct) is the **trade name** on the submission, e.g.
  `"a2z-Unified-Triage"`.
- `DEVICENAME` (106 distinct, 490 null) is the **FDA classification name** for the
  product code, e.g. `"Radiological Computer-Assisted Triage And Notification
  Software"`.

A device-name search must hit the first; a "what kind of device" rollup must hit
the second. In the modeled layer these become `device_trade_name` and
`device_classification_name`.

### `REGULATIONNUMBER` is a float and is corrupted

Stored as `float64`, so trailing zeros are lost: CFR 892.2050 is stored as
`892.205`, 886.1100 as `886.11`. The values no longer round-trip to valid
regulation numbers. Must be read as string from source, or dropped for V1.
596 of 1367 are null regardless.

### `DATE RECEIVED` is a string, `DECISION DATE` is a datetime

Mixed types for the two date columns. Both cast explicitly in the model.

### Fields that are null for PMA only

`SUBMISSION TYPE`, `COUNTRY`, `THIRDPARTY` are null on exactly the 79 PMA rows.
Not an issue once PMA is excluded, but do not read those nulls as "missing data"
in the full file.

## Company mapping — `clean_name`

The owner-supplied mapping collapses 559 distinct `APPLICANT` strings into 485
`clean_name` values across the 510(k) rows. It does real work on the large
multinationals: 15 APPLICANT variants collapse to `GE Healthcare`, 13 to
`Philips`, 8 to `Siemens`. No `clean_name` retains a legal suffix.

Provenance is tracked in `clean_name_source`:

| source | rows |
|---|---|
| ming-mapping | 974 |
| pre-existing | 193 |
| cleaning_script | 182 |
| AI_suggested | 12 |
| manual_review | 6 |

### Residual defects

Six pairs differ only by case or punctuation and are certainly the same company.
They affect 45 rows and will split company-scoped answers:

| variant A | variant B | rows |
|---|---|---|
| `Ischemaview` (1) | `Ischema View` (19) | 20 |
| `Viz.Ai` (9) | `Viz. Ai` (1) | 10 |
| `Qure.AI` (7) | `Qure. Ai` (1) | 8 |
| `Bodyvision Medical` (1) | `Body Vision Medical` (2) | 3 |
| `Aiq Solutions` (1) | `AIQ Solutions` (1) | 2 |
| `CorVista Health` (1) | `Corvista Health` (1) | 2 |

A further 18 groups share a leading token and need a human ruling, because some
are the same company and some are genuinely different — `Iterative Scopes` and
`Iterative Health` are one company renamed, while `Circle Cardiovascular Imaging`
and `Circle Neurovascular Imaging` are not the same product line. These are
recorded as open questions rather than auto-merged.

The 12 `AI_suggested` rows are unreviewed LLM output and should be confirmed
before V1 freezes.
