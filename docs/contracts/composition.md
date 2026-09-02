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

## Cohort layer: collapse FDA to first approval, then join

Owner convention, 2026-08-27, and the standard way to make this join safe.

`funding_vs_first_clearance()` reduces FDA to **one row per company at its first
clearance** *before* joining deals. The FDA side is then a single row, so the join
is 1:many and `sum(deal_size_usd_m)` cannot double-count. It reaches the same
guarantee as the per-company fact lists, expressed as SQL — and unlike the fact
lists it scales to set-level questions.

```sql
WITH first_clearance AS (
    SELECT company_name,
           any_value(pb_company_id) AS pb_company_id,
           min(decision_date)       AS first_clearance,
           count(*)                 AS total_clearances
    FROM fda_510k GROUP BY company_name
)
SELECT ... FROM first_clearance fc
LEFT JOIN deals d ON d.company_id = fc.pb_company_id
```

**Company x first approval is the analytical unit** for every funding-versus-
approval question. Returns one row per company:

| column | meaning |
|---|---|
| `first_clearance`, `total_clearances` | the FDA anchor |
| `rounds_before`, `capital_before_usd_m` | venture rounds strictly before it |
| `rounds_after`, `capital_after_usd_m` | on or after |
| `undated_rounds` | rounds that cannot be placed in time, excluded from both |

Four properties, each asserted by a test:

- **One row per company** — 459 rows, 459 companies.
- **`LEFT JOIN`, never `INNER`.** All 459 appear, including companies with no
  funding data; an inner join would delete them from every cohort statistic.
- **Totals reconcile** with the underlying deal table — no inflation.
- **`NULL`, not `0`,** where capital is unknown. A company with no recorded rounds
  has not raised nothing.

Round counts reconcile exactly: `rounds_before + rounds_after + undated_rounds`
equals the company's venture-round count, and the cohort path agrees with
`company_profile()` company by company.

What this unlocks — the plan's §16 end-state query is now one filter:

> 289 companies have venture funding before their first AI clearance.
> Median $7.8m, mean $40.8m. **241 raised under $50m.**

## The router

`research(question)` is the single entry point. The model is asked *what kind of
question this is*, never which tables to use — the route determines that, and the
guard enforces it.

| route | handler | tables |
|---|---|---|
| `fda` | text-to-SQL | `fda_510k` |
| `pitchbook` | text-to-SQL | `pb_deals`, `pb_companies` |
| `timeline` | text-to-SQL | `company_funding_timeline` |
| `profile` | deterministic composition | both, joined in code |
| `refuse` | — | none |

**The three SQL sources hold disjoint table sets**, asserted by a test, so a route
is a hard boundary rather than a suggestion. A `timeline` query cannot re-join
`fda_510k` and reintroduce the fan-out the table exists to remove; the guard
rejects it.

`profile` is not a SQL route. "Tell me about Aidoc" wants every fact about one
entity from every source — assembly, not a query — so it runs
`company_profile()` and generates no SQL at all.

Routing is content-hash cached like every other model call, so a repeated question
costs nothing and consumes no budget.

## Views, not a materialised table (changed 2026-09-02)

The cross-source layer was a table that pre-aggregated deals into before/after
buckets. That made "average time from first funding to first approval"
unanswerable: the deal dates existed in the inputs and did not survive the
aggregation.

The lesson is a separation I had not made:

| decision | necessary? |
|---|---|
| **fix the grain** — one row per company at first approval | **yes** — this is what prevents fan-out |
| **pre-aggregate the measures** — counts and sums only | **no** — a bet on which questions would be asked |

Only the first is load-bearing. Views fix the grain without the bet, cannot go
stale, and need no rebuild. `create_views()` now defines two, both venture-only
and both anchored on first approval, with the company view derived from the deal
view so they cannot drift.

Attaching *company* attributes to *deal* rows is many-to-one and does not fan out
— verified: summing `deal_size_usd_m` over `v_company_deals` reproduces the deal
table's total exactly. What it does introduce is a counting trap: company columns
repeat per deal row and must never be summed. Documented in the contract and
pinned by a test.

## Not yet built

- **An eval set for the `timeline` route.** FDA and PitchBook each have frozen
  sets; the cross-source route has none, so its answers are unmeasured.
- **Narrative synthesis.** Evidence is assembled but not written up; `profile`
  currently returns a one-line summary and the fact list.
- **Post-clearance trajectory questions** beyond the before/after split — e.g.
  time from clearance to next round.
- **Multi-route questions.** One question resolves to one route; a question
  genuinely needing two would need decomposition.
