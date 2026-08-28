# Open question: company mapping defects

Status: **awaiting owner ruling.** Blocks freezing the V1 company dimension.
Raised 2026-08-27 from `docs/data-profile-v1.md`.

Mark each row with a decision. `MERGE -> <name>` picks a winner, `KEEP SEPARATE`
leaves both, `FIX -> <name>` supplies a name that appears in neither.

---

## Part A — RESOLVED 2026-08-27 by precedence rule

Owner ruling: **`ming-mapping` is the authoritative source.** Where one
`applicant_raw` carried several `clean_name` values, the value from the
highest-precedence source wins. Order is in `SOURCE_PRECEDENCE`
(`src/fda_agent/config.py`):

```
ming-mapping > manual_review > pre-existing > cleaning_script > AI_suggested
```

This settled **13 of the 16** cases and is enforced in the loader, so the class
cannot recur silently. Distinct companies went 485 -> 473.

### RESOLVED 2026-08-27 by the PitchBook bridge

All three were settled by external evidence rather than judgment. PitchBook
company IDs are independent proof of entity identity:

| applicant | PitchBook IDs | verdict |
|---|---|---|
| `ITERATIVE SCOPES, INC.` | both `231656-59` | **same company** — merged |
| `SOFTWARE NEMOTEC S.L.` | both `140027-05` | **same company** — merged |
| `SAMSUNG ELECTRONICS CO., LTD.` | `59366-98` vs `51387-94` | **different companies** — stays split, correctly |

Samsung remains the one applicant string spanning two companies, and that is now
understood rather than unresolved: Samsung Electronics and Samsung Medison are
separate filers.

**Suspected bridge defect, flagged not fixed.** Both Samsung rows are filed by
`SAMSUNG ELECTRONICS CO., LTD.`, but the bridge sends K230292 — "Samsung ECG
Monitor Application with Irregular Heart Rhythm Notification", a Galaxy Watch
feature — to Samsung *Medison*, an ultrasound company. The other row (K201560,
Auto Lung Nodule Detection) goes to Samsung Electronics. K230292 looks
misattributed. Owner decision required; the loader does not second-guess the
bridge.

### Part B superseded: 13 merges performed

The bridge resolved 13 of the Part B pairs, including cases the precedence rule
structurally could not reach — precedence fixes one applicant mapping to several
names, but `Ischema View` and `Ischemaview` are *different* applicant strings, and
nothing inside the FDA data distinguishes that from two genuinely different firms.

Surviving spelling is the most frequent FDA one; ties break toward PitchBook's
current name, then alphabetically. Distinct companies: 473 → **459**.

### Still open: two parent/subsidiary rollups

Not merged, because collapsing them is a corporate-hierarchy decision rather than
a name-variant fix — the standing question from the original Part B.

| PitchBook parent | FDA filers | clearances |
|---|---|---|
| GE HealthCare Technologies | `GE Healthcare` / `Ge Hangwei Medical Systems` / `Ge Medical Systems Ultrasound And Primary Care Diagnostic` | 95 / 2 / 1 |
| FujiFilm | `Fujifilm` / `Fujifilm Healthcare` | 6 / 4 |

Merging would take GE Healthcare to 98 clearances and Fujifilm to 10, and would
change the top-10 company ranking. Held in `ROLLUP_COMPANY_IDS`
(`src/fda_agent/ingest.py`) pending a ruling.

### Two outcomes of the rule worth a second look

Both follow the ruling as given; flagging the results, not reopening the rule.

- **`KOIOS MEDICAL, INC.` now resolves to `Clearview`.** ClearView Diagnostics
  appears to be the company's *former* name, so the rule canonicalizes to the
  older identity rather than the current one. If canonical names should be
  current-name, this one wants an override.
- **`SPECTRUM DYNAMICS MEDICAL, LTD.` — an automated pass overrode a human one.**
  `manual_review` said `Spectrum Dynamics`; `ming-mapping` won with
  `Spectrum Dynamics Medical`. The outcome matches the filed name so it looks
  right here, but the precedence order does rank an automated pass above human
  review generally.

## Part B — distinct APPLICANTs, possibly one company (needs judgment)

Same leading token, different `APPLICANT` strings. Some are one company, some are
not. Not auto-merged.

| # | variants | rows | note | decision |
|---|---|---|---|---|
| B1 | `Canon` / `Canon Medical Systems` / `Canon Medical Informatics` | 1 / 38 / 1 | parent vs subsidiaries | |
| B2 | `Samsung` / `Samsung Medison` | 1 / 18 | parent vs subsidiary | |
| B3 | `Fujifilm` / `Fujifilm Healthcare` | 7 / 3 | parent vs subsidiary | |
| B4 | `Dentsply` / `Dentsply Sirona` | 1 / 1 | same company, inconsistent form | |
| B5 | `CorVista Health` / `Corvista Health` | 1 / 1 | `ANALYTICS FOR LIFE` renamed to CorVista | |
| B6 | `EarliTec Diagnostics` / `Earlitec` | 2 / 1 | same company, casing | |
| B7 | `Iterative Health` / `Iterative Scopes` | 4 / 2 | Iterative Scopes renamed to Iterative Health | |
| B8 | `Medicrea` / `Medicrea International` | 3 / 1 | latter is `(MEDTRONIC)` — acquired | |
| B9 | `Infervision` / `Infervision Medical Technology` | 1 / 2 | Beijing entity vs successor | |
| B10 | `Scopio` / `Scopio Labs` | 2 / 1 | same, see A12 | |
| B11 | `Raysearch` / `Raysearch Laboratories` | 1 / 7 | same, see A10 | |
| B12 | `Spectrum Dynamics` / `Spectrum Dynamics Medical` | 1 / 1 | same, see A15 | |
| B13 | `Springbok` / `Springbok Analytics` | 1 / 1 | `SPRINGBOK, INC. (DBA SPRINGBOK ANALYTICS)` | |
| B14 | `Empatica` / `Empatica S. R. L` | 2 / 2 | same, see A5 | |
| B15 | `Smart Soft Healthcare` / `Smart Soft Healthcare Ad` | 2 / 1 | same, see A13 | |
| B16 | `Circle Cardiovascular Imaging` / `Circle Neurovascular Imaging` | 9 / 1 | **likely distinct** | |
| B17 | `Intelligent Retinal Imaging Systems` / `Intelligent Ultrasound` | 1 / 1 | **distinct** | |
| B18 | `Surgical Information Sciences` / `Surgical Theater` | 7 / 1 | **distinct** | |

---

## Part C — unreviewed `AI_suggested` mappings (12 rows)

LLM-generated, never human-confirmed. Per the invariants these should not enter a
frozen V1 unconfirmed. They look correct on inspection; the ask is a yes/no.

| APPLICANT | clean_name | ok? |
|---|---|---|
| `7D SURGICAL ULC` | `7D Surgical` | |
| `AUDAX D.O.O.` | `Audax` | |
| `CARISTO DIAGNOSTICS , LTD.` | `Caristo Diagnostics` | |
| `EKO.AI PTE LTD. D/B/A US2.AI` | `Us2.ai` | see A4 |
| `EVER FORTUNE.AI, CO., LTD.` | `Ever Fortune.ai` | |
| `ICARDIO.AI` | `Icardio.ai` | |
| `LIGENCE UAB` | `Ligence` | |
| `NEURO EVENT LABS OY` | `Neuro Event Labs` | |
| `SCOPIO LABS , LTD.` | `Scopio Labs` | see A12 |
| `SMART ALFA TEKNOLOJI SAN. VE TIC. A.S.` | `Smart Alfa Teknoloji` | |

---

## Standing decision needed: parent vs subsidiary

B1, B2, B3, B8, B9 all turn on one rule that should be decided once and applied
everywhere, not case by case:

> When a subsidiary files under its own name, does it roll up to the parent?

It changes company rankings materially — Canon Medical Systems' 38 clearances
either sit under `Canon` or don't. The plan defers corporate hierarchy to V2
entity resolution, which argues for **keeping filed entities separate in V1** and
adding a `parent_company` column later rather than collapsing now and losing the
distinction irreversibly.
