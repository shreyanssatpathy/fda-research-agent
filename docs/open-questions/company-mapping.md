# Open question: company mapping defects

Status: **awaiting owner ruling.** Blocks freezing the V1 company dimension.
Raised 2026-08-27 from `docs/data-profile-v1.md`.

Mark each row with a decision. `MERGE -> <name>` picks a winner, `KEEP SEPARATE`
leaves both, `FIX -> <name>` supplies a name that appears in neither.

---

## Part A — the mapping is not a function (16 cases, 61 rows)

These are not judgment calls. The **same** `APPLICANT` string produces **different**
`clean_name` values on different rows, so identical input yields different output.
Any company-scoped query splits or double-counts depending on which rows it hits.

Root cause is visible in `clean_name_source`: 14 of the 16 mix two provenances
(usually `ming-mapping` against `pre-existing`), so passes were unioned without a
precedence rule. Fixing precedence fixes most of this class permanently.

| # | APPLICANT | maps to | decision |
|---|---|---|---|
| A1 | `AXIAL MEDICAL PRINTING LIMITED` | `Axial3D` (2) / `Axial Medical Printing` (1) | |
| A2 | `BODYVISION MEDICAL , LTD.` | `Body Vision Medical` (2) / `Bodyvision Medical` (1) | |
| A3 | `EKO HEALTH, INC.` | `Eko Health` (1) / `Eko` (1) | |
| A4 | `EKO.AI PTE LTD. D/B/A US2.AI` | `Eko.ai` (2) / `Us2.ai` (1) | |
| A5 | `EMPATICA S.R.L.` | `Empatica S. R. L` (2) / `Empatica` (2) | |
| A6 | `FUJIFILM HEALTHCARE CORPORATION` | `Fujifilm Healthcare` (1) / `Fujifilm` (1) | |
| A7 | `ITERATIVE SCOPES, INC.` | `Iterative Scopes` (2) / `Iterative Health` (2) | |
| A8 | `KOIOS MEDICAL, INC.` | `Clearview` (2) / `Koios Medical` (1) | |
| A9 | `QURE.AI TECHNOLOGIES` | `Qure.AI` (7) / `Qure. Ai` (1) | |
| A10 | `RAYSEARCH LABORATORIES AB (PUBL)` | `Raysearch Laboratories` (7) / `Raysearch` (1) | |
| A11 | `SAMSUNG ELECTRONICS CO., LTD.` | `Samsung Medison` (1) / `Samsung` (1) | |
| A12 | `SCOPIO LABS , LTD.` | `Scopio` (2) / `Scopio Labs` (1) | |
| A13 | `SMART SOFT HEALTHCARE AD` | `Smart Soft Healthcare` (1) / `Smart Soft Healthcare Ad` (1) | |
| A14 | `SOFTWARE NEMOTEC S.L.` | `Nemotec` (1) / `Software Nemotec` (1) | |
| A15 | `SPECTRUM DYNAMICS MEDICAL, LTD.` | `Spectrum Dynamics Medical` (1) / `Spectrum Dynamics` (1) | |
| A16 | `VIZ. AI, INC.` | `Viz.Ai` (9) / `Viz. Ai` (1) | |

Two worth a second look:

- **A8** — `KOIOS MEDICAL, INC.` mapping to `Clearview` looks like a product name
  leaked into the company field rather than a rename.
- **A11** — `SAMSUNG ELECTRONICS CO., LTD.` mapping to `Samsung Medison` is wrong
  in one direction regardless: the parent and the medical subsidiary are distinct
  legal entities and Samsung Medison files under its own name elsewhere in the data.

---

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
