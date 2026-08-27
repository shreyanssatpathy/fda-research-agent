# Evaluation

## `golden_v1.yaml` — 38 cases

**Status: FROZEN 2026-08-27 (`frozen: true`).**

Integrity is pinned in `golden_v1.sha256` and asserted by `tests/test_evals.py`.
Any edit to the file fails the suite — which is the point. Changing a frozen case
requires updating the hash deliberately and recording the ruling below.

The reference SQL is hand-written and is the thing under review. Every expected
answer was produced by executing that SQL against the built database, so no number
in the file was authored by hand — if the reference SQL misreads a question, the
expected answer is wrong in a visible way rather than a plausible one.

Once frozen, `frozen: true` is set and the file is never regenerated or edited to
improve a score (CLAUDE.md). A case found to be wrong is corrected only by an
explicit, dated ruling in this README.

### Coverage

| category | n | what it tests |
|---|---|---|
| `count` | 5 | simple filters and aggregates |
| `company` | 5 | company scoping via `company_name`, not `applicant_raw` |
| `first_clearance` | 4 | `min(decision_date)` grouped by company |
| `time_series` | 4 | annual counts, review-time deltas, both date columns |
| `device` | 6 | product-code rollups, trade-name search, null-heavy columns |
| `geography` | 3 | country and state filters |
| `refuse_out_of_range` | 2 | 2026 questions — must not answer zero |
| `refuse_no_denominator` | 3 | rates and shares with no baseline in the data |
| `refuse_not_in_data` | 3 | funding, recalls, PMA — out of V1 scope |
| `clarify` | 3 | ambiguous company references |

**Eight of the 38 cases must be refused and three must ask for clarification.** A
system that answers all 38 has failed, not passed. These are the cases that catch
confident fabrication, which is the failure mode the evidence layer exists to
prevent — and they are the reason the set is not just SQL correctness checks.

### Cases worth reviewing closely

- **F01–F04** turn on the narrow claim: "first AI-enabled 510(k) in this dataset",
  never "first FDA clearance". The SQL cannot express that caveat; the answer text
  must carry it.
- **D02** groups on `lower(medical_specialty)` because `pathology` is lowercase in
  the data while every other value is title-case.
- **D06** counts Class III devices where `device_class` is 34% null. The expected
  answer is 33, but a correct response should say the denominator is incomplete.
- **A01/A02** are ambiguous *because of known data defects* — Samsung and Canon are
  unresolved splits. If those get ruled on, these two cases change meaning and
  should be revisited deliberately.


## Amendments

None yet. Record any change to a frozen case here with its date and reason.

## Running

```
python evals/run.py            # sample mode: 8 cases
python evals/run.py --full     # all 38
```

Sample mode is the default per CLAUDE.md. The 8 sampled cases deliberately span
both answerable and unanswerable questions — a sample of only answerable ones
would hide exactly the failures that matter.

Generation requires an Anthropic API key (`ANTHROPIC_API_KEY`). Everything else —
the loader, the SQL guard, and the scorer — runs offline.

### How scoring works

- **Answerable case**: the model must choose `sql`; the SQL must survive the
  guard, execute, and produce the same row count and the same values in the same
  order. Column *names* are ignored (`n` vs `total` is style).
- **Unanswerable case**: the model must choose `refuse` (or `clarify` for the
  ambiguous ones). Producing SQL scores as a **failure**, not partial credit —
  answering an unanswerable question confidently is the failure mode this project
  exists to prevent.
- Declining an answerable question is also a failure. Refusing everything scores
  no better than answering everything.

`tests/test_eval_runner.py` asserts that every reference query scores as a pass
against its own frozen answer. If the scorer cannot recognise the known-correct
SQL as correct, no score it produces means anything.
