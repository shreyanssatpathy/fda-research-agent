"""Author the PitchBook golden eval set.

Standalone: no cross-source questions. The PitchBook tool must score well on its
own before anything joins it to FDA data, so a failure is attributable to one
layer.

Same discipline as the FDA sets — reference SQL is hand-written from the contract
and is the thing under review; expected answers are executed, never authored.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from fda_agent.db import connect  # noqa: E402
from normalize import normalize_cell  # noqa: E402

CO = (
    "(lower(c.company_name_pb) = lower('{n}') "
    "OR lower(c.company_name_pb) LIKE lower('{n}') || ' %')"
)

# (id, category, question, expects, reference_sql, note)
CASES = [
    # --- counting -------------------------------------------------------------
    ("P01", "count", "How many funding deals are in the database?", "answer",
     "SELECT count(*) AS n FROM pb_deals", None),
    ("P02", "count", "How many venture rounds are there?", "answer",
     "SELECT count(*) AS n FROM pb_deals WHERE is_venture_round", None),
    ("P03", "count", "How many companies have funding data?", "answer",
     "SELECT count(DISTINCT company_id) AS n FROM pb_deals", None),
    ("P04", "count", "How many companies are venture-backed?", "answer",
     "SELECT count(*) AS n FROM pb_companies WHERE in_qualified_universe", None),

    # --- capital: the rule that matters most ----------------------------------
    ("P05", "capital", "How much venture funding has Aidoc raised?", "answer",
     "SELECT round(sum(d.deal_size_usd_m), 2) AS venture_usd_m FROM pb_deals d "
     "JOIN pb_companies c USING (company_id) "
     f"WHERE {CO.format(n='Aidoc')} AND d.is_venture_round",
     "Must filter is_venture_round; the total is a floor (disclosed rounds only)."),
    ("P06", "capital", "Which companies have raised the most venture funding?", "answer",
     "SELECT c.company_name_pb, round(sum(d.deal_size_usd_m), 1) AS venture_usd_m "
     "FROM pb_deals d JOIN pb_companies c USING (company_id) "
     "WHERE d.is_venture_round GROUP BY 1 ORDER BY venture_usd_m DESC, 1 LIMIT 10",
     "Unfiltered, this ranks Apple first on share repurchases."),
    ("P07", "capital", "What is the median venture round size?", "answer",
     "SELECT round(median(deal_size_usd_m), 1) AS median_usd_m FROM pb_deals "
     "WHERE is_venture_round", None),
    ("P08", "capital", "How much total venture capital is in this dataset?", "answer",
     "SELECT round(sum(deal_size_usd_m), 1) AS venture_usd_m FROM pb_deals "
     "WHERE is_venture_round", None),

    # --- deal structure -------------------------------------------------------
    ("P09", "deals", "What types of deals are most common?", "answer",
     "SELECT deal_type, count(*) AS n FROM pb_deals GROUP BY 1 "
     "ORDER BY n DESC, deal_type LIMIT 10", None),
    ("P10", "deals", "How many venture rounds happened each year since 2020?", "answer",
     "SELECT year(deal_date) AS yr, count(*) AS n FROM pb_deals "
     "WHERE is_venture_round AND year(deal_date) >= 2020 GROUP BY 1 ORDER BY yr", None),
    ("P11", "deals", "How many deals have an estimated rather than actual size?", "answer",
     "SELECT count(*) AS n FROM pb_deals WHERE deal_size_status = 'Estimated'", None),
    ("P12", "deals", "Show all venture rounds for Butterfly Network.", "answer",
     "SELECT d.deal_date, d.deal_type, d.deal_size_usd_m FROM pb_deals d "
     "JOIN pb_companies c USING (company_id) "
     f"WHERE {CO.format(n='Butterfly Network')} AND d.is_venture_round "
     "ORDER BY d.deal_date", None),

    # --- companies ------------------------------------------------------------
    ("P13", "companies", "Which countries are these companies headquartered in?", "answer",
     "SELECT hq_country, count(*) AS n FROM pb_companies GROUP BY 1 "
     "ORDER BY n DESC, hq_country LIMIT 10", None),
    ("P14", "companies", "How many companies were founded after 2015?", "answer",
     "SELECT count(*) AS n FROM pb_companies WHERE year_founded > 2015", None),
    ("P15", "companies", "What financing statuses do these companies have?", "answer",
     "SELECT financing_status, count(*) AS n FROM pb_companies GROUP BY 1 "
     "ORDER BY n DESC, financing_status", None),

    # --- refusals: needs FDA data --------------------------------------------
    ("P16", "refuse_needs_fda", "How much did companies raise before their first FDA clearance?",
     "refuse", None, "Needs fda_510k, which this tool cannot query."),
    ("P17", "refuse_needs_fda", "Which company got FDA clearance fastest after its Series A?",
     "refuse", None, "Needs FDA clearance dates."),
    ("P18", "refuse_needs_fda", "How many AI devices has Aidoc cleared?", "refuse", None,
     "Device counts are FDA data, not funding data."),

    # --- refusals: not in the data -------------------------------------------
    ("P19", "refuse_not_in_data", "Which investors backed the most companies?", "refuse", None,
     "Investor names are not in these tables."),
    ("P20", "refuse_not_in_data", "How much revenue do these companies generate?", "refuse", None,
     "Revenue is not in the loaded columns."),
    ("P21", "refuse_not_in_data", "How much did GE Healthcare raise?", "refuse", None,
     "GE is a Corporation, outside the venture universe. Out of scope, not zero."),

    # --- clarify --------------------------------------------------------------
    ("P22", "clarify", "How much has Samsung raised?", "clarify", None,
     "Samsung matches more than one company."),
    ("P23", "clarify", "What is this company's total funding?", "clarify", None,
     "No company named."),
]


def main() -> int:
    out = []
    with connect() as con:
        for cid, cat, q, expects, sql, note in CASES:
            entry = {"id": cid, "category": cat, "question": q,
                     "expects": expects, "note": note, "reference_sql": sql}
            if expects == "answer":
                df = con.execute(sql).df()
                entry["expected_answer"] = {
                    "row_count": len(df),
                    "columns": list(df.columns),
                    "rows": [{k: normalize_cell(v) for k, v in r.items()}
                             for r in df.head(12).to_dict("records")],
                    "truncated": len(df) > 12,
                }
            out.append(entry)

    doc = {
        "schema_version": "pb-1.0.0",
        "source": "pitchbook",
        "frozen": False,
        "note": ("Draft. Standalone PitchBook cases only — no cross-source questions. "
                 "Expected answers were executed, not hand-written."),
        "cases": out,
    }
    path = Path(__file__).parent / "golden_pitchbook.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))
    from collections import Counter
    c = Counter(x["expects"] for x in out)
    print(f"wrote {path} — {len(out)} cases")
    for k, v in sorted(c.items()):
        print(f"  expects {k:10} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
