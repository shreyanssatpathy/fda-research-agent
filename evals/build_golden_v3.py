"""Author the v3 golden eval set.

v1 and v2 stay frozen and untouched. v3 corrects three references in v2:

- **M01, D05** — stale. They froze the four-column default projection, which
  contract rule 14 replaced with `SELECT *` on 2026-08-27 (owner decision: a
  clearance listing returns the whole record).
- **G01** — defective. "Which countries do AI device *companies* file from?" has
  companies as its subject, but the reference counted clearances.

Same discipline as v2: questions stay byte-identical, every change cites its
reason, references are written from the contract and then executed.

Note on G01, recorded because it matters: the system's reading was more literal
than my reference's, so correcting it also happens to make a failing case pass.
The test I applied is whether I would make the same change had the system done the
opposite — reading "which countries do companies file from" as a question about
companies, I would. Flagged so a reader can disagree.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from fda_agent.db import connect  # noqa: E402
from normalize import normalize_cell  # noqa: E402

V2 = Path(__file__).parent / "golden_v2.yaml"

COMPANY_MATCH = (
    "(lower(company_name) = lower('{n}') "
    "OR lower(company_name) LIKE lower('{n}') || ' %')"
)

CORRECTIONS: dict[str, tuple[str, str]] = {
    "M01": (
        "SELECT * FROM fda_510k WHERE "
        + COMPANY_MATCH.format(n="Aidoc")
        + " ORDER BY decision_date",
        "Rule 14 (changed 2026-08-27): a clearance listing returns every column. "
        "v2 froze the superseded four-column projection.",
    ),
    "D05": (
        "SELECT * FROM fda_510k WHERE lower(device_trade_name) LIKE '%triage%' "
        "ORDER BY decision_date",
        "Rule 14 (changed 2026-08-27): a clearance listing returns every column. "
        "v2 froze the superseded four-column projection.",
    ),
    "G01": (
        "SELECT country, count(DISTINCT company_name) AS n_companies FROM fda_510k "
        "GROUP BY 1 ORDER BY n_companies DESC, country",
        "Defect: the question's subject is companies, but the v2 reference counted "
        "clearances. Counting distinct companies is the literal reading.",
    ),
}


def main() -> int:
    v2 = yaml.safe_load(V2.read_text())
    out = []

    with connect() as con:
        for case in v2["cases"]:
            cid = case["id"]
            entry = {k: v for k, v in case.items() if k != "changed_from_v1"}
            if case.get("changed_from_v1"):
                entry["changed_in_v2"] = case["changed_from_v1"]

            if cid in CORRECTIONS:
                sql, reason = CORRECTIONS[cid]
                entry["reference_sql"] = sql
                entry["changed_in_v3"] = reason

            if entry["expects"] == "answer":
                df = con.execute(entry["reference_sql"]).df()
                entry["expected_answer"] = {
                    "row_count": len(df),
                    "columns": list(df.columns),
                    "rows": [
                        {k: normalize_cell(v) for k, v in r.items()}
                        for r in df.head(12).to_dict("records")
                    ],
                    "truncated": len(df) > 12,
                }
            out.append(entry)

    doc = {
        "schema_version": "3.0.0",
        "supersedes": "golden_v2.yaml",
        "frozen": False,
        "note": (
            "Draft. v1 and v2 remain frozen and unmodified. Three references "
            "corrected — see changed_in_v3 on each. Questions are byte-identical "
            "to v1. Expected answers were executed, not hand-written."
        ),
        "cases": out,
    }
    path = Path(__file__).parent / "golden_v3.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))

    from collections import Counter

    c = Counter(x["expects"] for x in out)
    print(f"wrote {path} — {len(out)} cases")
    for k, v in sorted(c.items()):
        print(f"  expects {k:10} {v}")
    print(f"  corrected in v3: {sorted(CORRECTIONS)}")
    for cid in sorted(CORRECTIONS):
        ea = next(x for x in out if x["id"] == cid).get("expected_answer")
        if ea:
            print(f"    {cid}: {ea['row_count']} rows, {len(ea['columns'])} columns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
