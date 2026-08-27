"""Author the V1 golden eval set.

Reference SQL here is hand-written and is the thing under review. Expected answers
are produced by executing it against the built database, so no expected value is
ever invented — every number in the frozen file came out of the data.

Run once to produce evals/golden_v1.yaml, then freeze. Per CLAUDE.md the frozen
set is never regenerated to make a score improve; a wrong case is fixed only by an
explicit, dated ruling.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))

from fda_agent.db import connect  # noqa: E402

# (id, category, question, reference_sql, note)
# reference_sql is None for cases the system must REFUSE rather than answer.
CASES = [
    # --- counting and filtering -------------------------------------------------
    ("C01", "count", "How many AI-enabled devices have been cleared through 510(k)?",
     "SELECT count(*) AS n FROM fda_510k", None),
    ("C02", "count", "How many distinct companies have AI 510(k) clearances?",
     "SELECT count(DISTINCT company_name) AS n FROM fda_510k", None),
    ("C03", "count", "How many AI devices were cleared in 2023?",
     "SELECT count(*) AS n FROM fda_510k WHERE decision_year = 2023", None),
    ("C04", "count", "How many AI clearances went through the Third Party Review Program?",
     "SELECT count(*) AS n FROM fda_510k WHERE third_party", None),
    ("C05", "count", "How many Special 510(k) submissions are there?",
     "SELECT count(*) AS n FROM fda_510k WHERE submission_type = 'Special'", None),

    # --- company-scoped ----------------------------------------------------------
    ("M01", "company", "Show all FDA clearances for Aidoc.",
     "SELECT regnumber, decision_date, device_trade_name, product_code FROM fda_510k "
     "WHERE lower(company_name) LIKE '%aidoc%' ORDER BY decision_date",
     "Company filters must use company_name, not applicant_raw."),
    ("M02", "company", "Which companies have the most AI device clearances?",
     "SELECT company_name, count(*) AS n FROM fda_510k GROUP BY 1 ORDER BY n DESC, company_name LIMIT 10", None),
    ("M03", "company", "How many clearances does GE Healthcare have?",
     "SELECT count(*) AS n FROM fda_510k WHERE company_name = 'GE Healthcare'",
     "15 applicant_raw spellings collapse here; filtering the raw field undercounts."),
    ("M04", "company", "How many companies have only ever had one AI clearance?",
     "SELECT count(*) AS n FROM (SELECT company_name FROM fda_510k GROUP BY 1 HAVING count(*) = 1)", None),
    ("M05", "company", "List companies with at least 10 AI clearances.",
     "SELECT company_name, count(*) AS n FROM fda_510k GROUP BY 1 HAVING count(*) >= 10 "
     "ORDER BY n DESC, company_name", None),

    # --- first clearance ---------------------------------------------------------
    ("F01", "first_clearance", "When did Viz.ai receive its first AI clearance?",
     "SELECT min(decision_date) AS first_clearance FROM fda_510k WHERE company_name = 'Viz.Ai'",
     "Answer must say 'first AI-enabled 510(k) in this dataset', not 'first FDA clearance'."),
    ("F02", "first_clearance", "Which companies received their first AI clearance in 2023?",
     "SELECT company_name, min(decision_date) AS first_clearance FROM fda_510k "
     "GROUP BY 1 HAVING year(min(decision_date)) = 2023 ORDER BY first_clearance, company_name", None),
    ("F03", "first_clearance", "How many companies entered the AI device market each year?",
     "SELECT year(first_clearance) AS yr, count(*) AS n FROM "
     "(SELECT company_name, min(decision_date) AS first_clearance FROM fda_510k GROUP BY 1) "
     "GROUP BY 1 ORDER BY yr", None),
    ("F04", "first_clearance", "Which company was the earliest to get an AI clearance?",
     "SELECT company_name, min(decision_date) AS first_clearance FROM fda_510k "
     "GROUP BY 1 ORDER BY first_clearance LIMIT 3", None),

    # --- time series -------------------------------------------------------------
    ("T01", "time_series", "How many AI devices were cleared annually since 2015?",
     "SELECT decision_year, count(*) AS n FROM fda_510k WHERE decision_year >= 2015 "
     "GROUP BY 1 ORDER BY decision_year",
     "The plan's headline question."),
    ("T02", "time_series", "How long does it take from submission to decision, on average?",
     "SELECT round(avg(date_diff('day', date_received, decision_date)), 1) AS avg_days FROM fda_510k",
     "Uses both date columns; must not confuse date_received with decision_date."),
    ("T03", "time_series", "Has review time gotten longer over the years?",
     "SELECT decision_year, round(median(date_diff('day', date_received, decision_date)), 1) AS median_days, "
     "count(*) AS n FROM fda_510k GROUP BY 1 ORDER BY decision_year", None),
    ("T04", "time_series", "How many AI clearances were there in the last full year of data?",
     "SELECT decision_year, count(*) AS n FROM fda_510k WHERE decision_year = "
     "(SELECT max(decision_year) FROM fda_510k) GROUP BY 1", None),

    # --- device / category -------------------------------------------------------
    ("D01", "device", "Which product codes have the most AI clearances?",
     "SELECT product_code, count(*) AS n FROM fda_510k GROUP BY 1 ORDER BY n DESC, product_code LIMIT 10",
     "Rollups use product_code; device_classification_name is 34% null."),
    ("D02", "device", "What medical specialties do AI devices fall under?",
     "SELECT lower(medical_specialty) AS specialty, count(*) AS n FROM fda_510k "
     "GROUP BY 1 ORDER BY n DESC, specialty",
     "Must compare case-insensitively; 'pathology' is lowercase in the data."),
    ("D03", "device", "How many AI clearances are in radiology?",
     "SELECT count(*) AS n FROM fda_510k WHERE lower(medical_specialty) = 'radiology'", None),
    ("D04", "device", "Which product code grew fastest between 2020 and 2025?",
     "SELECT product_code, "
     "sum(CASE WHEN decision_year = 2020 THEN 1 ELSE 0 END) AS n_2020, "
     "sum(CASE WHEN decision_year = 2025 THEN 1 ELSE 0 END) AS n_2025 "
     "FROM fda_510k GROUP BY 1 HAVING n_2020 > 0 ORDER BY (n_2025 - n_2020) DESC, product_code LIMIT 5", None),
    ("D05", "device", "Find AI devices with 'triage' in the device name.",
     "SELECT regnumber, company_name, device_trade_name FROM fda_510k "
     "WHERE lower(device_trade_name) LIKE '%triage%' ORDER BY regnumber",
     "Device-name search means device_trade_name, not device_classification_name."),
    ("D06", "device", "How many Class III AI devices were cleared?",
     "SELECT count(*) AS n FROM fda_510k WHERE device_class = '3'",
     "device_class is 34% null; the answer should say so."),

    # --- geography ---------------------------------------------------------------
    ("G01", "geography", "Which countries do AI device companies file from?",
     "SELECT country, count(*) AS n FROM fda_510k GROUP BY 1 ORDER BY n DESC, country LIMIT 10", None),
    ("G02", "geography", "How many AI clearances come from non-US companies?",
     "SELECT count(*) AS n FROM fda_510k WHERE country <> 'US'", None),
    ("G03", "geography", "Which US state has the most AI device clearances?",
     "SELECT state, count(*) AS n FROM fda_510k WHERE country = 'US' AND state IS NOT NULL "
     "GROUP BY 1 ORDER BY n DESC, state LIMIT 5", None),

    # --- refusals ----------------------------------------------------------------
    ("R01", "refuse_out_of_range", "How many AI devices were cleared in 2026?", None,
     "Data ends 2025-12-30. Must answer 'outside the data range', never 0."),
    ("R02", "refuse_out_of_range", "What is the AI clearance trend so far this year?", None,
     "Current year is 2026 and has no rows. Must state the coverage boundary."),
    ("R03", "refuse_no_denominator", "What percentage of all FDA clearances are AI-enabled?", None,
     "No non-AI baseline in the dataset. Must refuse, not compute against an AI-only base."),
    ("R04", "refuse_no_denominator", "What is the clearance rate for AI device submissions?", None,
     "Only successful clearances present; no denied submissions exist to form a rate."),
    ("R05", "refuse_no_denominator", "How many AI device submissions were rejected by the FDA?", None,
     "No NSE decisions in the data. Absence is not zero."),
    ("R06", "refuse_not_in_data", "How much funding did Aidoc raise before its first clearance?", None,
     "Investment data is out of V1 scope. Must not guess."),
    ("R07", "refuse_not_in_data", "Which AI devices were later recalled?", None,
     "No recall data in this table."),
    ("R08", "refuse_not_in_data", "How many PMA approvals do these companies have?", None,
     "V1 is 510(k) only; PMA rows were excluded at load."),

    # --- ambiguous, should clarify ------------------------------------------------
    ("A01", "clarify", "Show me Samsung's clearances.", None,
     "Samsung / Samsung Medison is an unresolved split; should ask which entity."),
    ("A02", "clarify", "How many devices has Canon cleared?", None,
     "Canon vs Canon Medical Systems vs Canon Medical Informatics; parent rollup is undecided."),
    ("A03", "clarify", "When did this company get approved?", None,
     "No company named. Must ask rather than pick one."),
]


# Kept in sync with the scorer by sharing one implementation; see normalize.py.
from normalize import normalize_cell as _scalar  # noqa: E402


def main() -> int:
    out = []
    with connect() as con:
        for case_id, category, question, sql, note in CASES:
            entry = {
                "id": case_id,
                "category": category,
                "question": question,
                "note": note,
            }
            if sql is None:
                entry["expects"] = "refusal_or_clarification"
                entry["reference_sql"] = None
                entry["expected_answer"] = None
            else:
                df = con.execute(sql).df()
                entry["expects"] = "answer"
                entry["reference_sql"] = sql
                entry["expected_answer"] = {
                    "row_count": len(df),
                    "columns": list(df.columns),
                    "rows": [
                        {k: _scalar(v) for k, v in r.items()}
                        for r in df.head(12).to_dict("records")
                    ],
                    "truncated": len(df) > 12,
                }
            out.append({k: v for k, v in entry.items() if v is not None or k in ("note",)})

    doc = {
        "schema_version": "1.0.0",
        "frozen": False,
        "note": (
            "Draft awaiting owner review. Once frozen, never regenerate or edit to "
            "improve a score (CLAUDE.md). Expected answers were executed against the "
            "database, not authored by hand."
        ),
        "cases": out,
    }
    path = Path(__file__).parent / "golden_v1.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))
    print(f"wrote {path} — {len(out)} cases")
    by = {}
    for c in out:
        by[c["category"]] = by.get(c["category"], 0) + 1
    for k, v in sorted(by.items()):
        print(f"  {k:24} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
