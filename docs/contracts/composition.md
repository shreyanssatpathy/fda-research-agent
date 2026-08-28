# Contract: cross-source composition

How FDA and PitchBook evidence is combined. Amend with a dated note.

## The join happens in Python, never in generated SQL

Neither text-to-SQL tool can reach the other's tables — enforced by the guard's
per-source allowlist, not by prompt instruction. `SELECT * FROM pb_deals JOIN
fda_510k` is rejected when the source is PitchBook, and vice versa.

Three reasons, in order of how much they cost when ignored:

**1. Fan-out is silent.** Aidoc has 31 clearances and 7 venture rounds. Joining
them produces 217 rows, and summing deal size over that gives **$13,028.1m instead
of $420.3m** — exactly 31x. The SQL is valid, the number is confidently wrong, and
nothing looks broken. `tests/test_compose.py::test_composition_does_not_inflate_capital`
asserts both the true figure and the inflation, so the hazard stays demonstrated.

**2. The guard cannot see it.** It validates table names and functions by walking
the AST. Cardinality is not a property it can check.

**3. Match confidence has nowhere to live in a JOIN.** A row is in or out; the
uncertainty vanishes from the result.

Composition therefore assembles **parallel fact lists keyed by the entity**. The
fan-out bug is unrepresentable rather than merely avoided.

## Entity resolution

`entity.resolve_company()` is deterministic — no LLM. The same string returns the
same entity every time, which is what makes the evidence auditable.

It searches three naming systems and returns whole companies:

| searched | why |
|---|---|
| `fda_510k.company_name` | the canonical FDA name |
| `fda_510k.applicant_raw` | the only record of former names after entity merging — `Ischemaview`, `Caption Health` |
| `pb_companies.company_name_pb` / `company_former_name` | names the FDA never used — `RapidAI` resolves to `Ischema View` |

**Several matches is ambiguity, not a set to aggregate.** `Samsung` returns two
entities and no profile; summing Samsung Electronics and Samsung Medison answers
a question nobody asked. `resolution.entity` raises rather than picking one.

## Facts carry provenance by construction

`Fact` requires `source` and `source_id` and raises without them. There is no path
that produces an unsourced fact.

| type | source | source_id |
|---|---|---|
| `FDA_CLEARANCE` | FDA | `regnumber` |
| `FUNDING_ROUND` | PitchBook | `deal_id` |

`Evidence.timeline()` returns dated facts only. Undated funding rounds are
excluded, never assigned a guessed date.

## Gaps are first-class

Three different situations that a zero would flatten into one:

| situation | statement |
|---|---|
| not in the bridge | "no funding data available — missing, not absent" |
| `in_qualified_universe = false` | "outside the venture universe by design — out of scope, not zero" |
| bridged, in universe, no rounds | "no disclosed venture rounds — a floor of zero, not a confirmed zero" |

Plus `funding_dates` when rounds exist but cannot be placed in time, which states
how many were excluded from before/after comparisons.

## Derived metrics

`capital_before_first_clearance_usd_m` sums venture rounds strictly before the
earliest clearance date. Undated rounds are excluded and reported as a gap. The
value is `None`, never `0`, when there are no qualifying rounds.

## Not yet built

- **A router.** Callers choose `company_profile()` explicitly; nothing yet decides
  which sources a free-text question needs.
- **Narrative synthesis.** Evidence is assembled but not yet written up.
- **Cohort questions.** "Which companies raised under $50m before clearance"
  needs a set-level path; only single-company profiles exist.
