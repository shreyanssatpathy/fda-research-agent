"""Author the v2 golden eval set.

v1 stays frozen and untouched. v2 is a separate, deliberately authored set that
corrects the nine reference-SQL defects catalogued in evals/README.md.

Discipline that makes this legitimate rather than metric-gaming:

1. Every corrected reference is written from the *contract's stated rules*, not
   from what the model happened to produce. The rule number justifying each
   change is recorded on the case.
2. Expected answers are executed against the database, never hand-written.
3. v1 is retained, so the two sets remain comparable and the change is auditable.

v2 also makes the expected action explicit per case rather than inferring it from
the category, and adds a `decline` expectation for questions where refusing and
asking for clarification are both defensible.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from fda_agent.db import connect  # noqa: E402
from normalize import normalize_cell  # noqa: E402

V1 = Path(__file__).parent / "golden_v1.yaml"

# id -> (new_reference_sql, rule_that_justifies_it)
# Only these nine differ from v1. Everything else is carried over verbatim.
CORRECTIONS: dict[str, tuple[str | None, str]] = {
    "F04": (
        "SELECT company_name, min(decision_date) AS first_clearance FROM fda_510k "
        "GROUP BY 1 ORDER BY first_clearance, company_name LIMIT 1",
        "Rule 7: a singular question returns one row. v1's LIMIT 3 was arbitrary.",
    ),
    "G03": (
        "SELECT state, count(*) AS n FROM fda_510k WHERE country = 'US' "
        "AND state IS NOT NULL GROUP BY 1 ORDER BY n DESC, state LIMIT 1",
        "Rule 7: 'which state has the most' is singular. v1's LIMIT 5 was arbitrary.",
    ),
    "G01": (
        "SELECT country, count(*) AS n FROM fda_510k GROUP BY 1 "
        "ORDER BY n DESC, country",
        "Rule 8 applies to rankings; this is an enumeration, so no LIMIT.",
    ),
    "T03": (
        "SELECT decision_year, "
        "round(median(date_diff('day', date_received, decision_date)), 1) AS median_days "
        "FROM fda_510k GROUP BY 1 ORDER BY decision_year",
        "Rule 12: a breakdown returns the grouping column and the measure only. "
        "v1 also returned a clearance count the question never asked for.",
    ),
    "T04": (
        "SELECT count(*) AS n FROM fda_510k WHERE decision_year = "
        "(SELECT max(decision_year) FROM fda_510k)",
        "Rule 7: 'how many' is scalar. v1 also returned decision_year.",
    ),
    "D02": (
        "SELECT medical_specialty, count(*) AS n FROM fda_510k GROUP BY 1 "
        "ORDER BY n DESC, medical_specialty",
        "Rule 13: return stored values. v1 lowercased in the SELECT, which the "
        "contract forbids — normalise for matching, not for output.",
    ),
    "D05": (
        "SELECT regnumber, decision_date, device_trade_name, product_code "
        "FROM fda_510k WHERE lower(device_trade_name) LIKE '%triage%' "
        "ORDER BY decision_date",
        "Rule 14: default projection for a clearance listing.",
    ),
    # Reclassified: the question is genuinely ambiguous, so there is no correct SQL.
    "D04": (None, "No correct SQL: 'grew fastest' may mean absolute or percentage "
                  "change. v1 silently resolved it as absolute."),
    "R02": (None, "Refusing and clarifying are both defensible; expectation widened "
                  "to `decline`."),
}

# Explicit expected action per case, replacing v1's inference from category.
DECLINE_EITHER = {"R02"}
EXPECT_CLARIFY = {"A01", "A02", "A03", "D04"}


def expected_action(case: dict) -> str:
    if case["id"] in DECLINE_EITHER:
        return "decline"
    if case["id"] in EXPECT_CLARIFY:
        return "clarify"
    if case["expects"] == "answer" and case["id"] not in CORRECTIONS:
        return "answer"
    sql, _ = CORRECTIONS.get(case["id"], (case.get("reference_sql"), ""))
    return "answer" if sql else "refuse"


def main() -> int:
    v1 = yaml.safe_load(V1.read_text())
    out = []

    with connect() as con:
        for case in v1["cases"]:
            cid = case["id"]
            sql = case.get("reference_sql")
            note = case.get("note")
            correction = None

            if cid in CORRECTIONS:
                sql, correction = CORRECTIONS[cid]

            action = expected_action(case)
            entry = {
                "id": cid,
                "category": case["category"],
                "question": case["question"],
                "expects": action,
                "note": note,
            }
            if correction:
                entry["changed_from_v1"] = correction

            if action == "answer":
                df = con.execute(sql).df()
                entry["reference_sql"] = sql
                entry["expected_answer"] = {
                    "row_count": len(df),
                    "columns": list(df.columns),
                    "rows": [
                        {k: normalize_cell(v) for k, v in r.items()}
                        for r in df.head(12).to_dict("records")
                    ],
                    "truncated": len(df) > 12,
                }
            else:
                entry["reference_sql"] = None
            out.append(entry)

    doc = {
        "schema_version": "2.0.0",
        "supersedes": "golden_v1.yaml",
        "frozen": False,
        "note": (
            "Draft. v1 remains frozen and unmodified. Nine reference queries were "
            "corrected against the contract's stated rules — each carries the rule "
            "in changed_from_v1. Expected answers were executed, not hand-written."
        ),
        "cases": out,
    }
    path = Path(__file__).parent / "golden_v2.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))

    from collections import Counter

    c = Counter(x["expects"] for x in out)
    print(f"wrote {path} — {len(out)} cases")
    for k, v in sorted(c.items()):
        print(f"  expects {k:10} {v}")
    print(f"  corrected from v1: {len(CORRECTIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
