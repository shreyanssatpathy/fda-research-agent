"""Author pitchbook-v2.

v1 stays frozen. Five references corrected, each against a stated contract rule
or definition — and three of the five are cases where the system was **more
correct than the reference**, which is the whole reason to fix the set rather
than the system.

Same discipline as every earlier revision: questions byte-identical, every change
carries its justification, expected answers executed rather than authored.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from fda_agent.db import connect  # noqa: E402
from normalize import normalize_cell  # noqa: E402

V1 = Path(__file__).parent / "golden_pitchbook.yaml"

CO = (
    "(lower(c.company_name_pb) = lower('{n}') "
    "OR lower(c.company_name_pb) LIKE lower('{n}') || ' %')"
)

CORRECTIONS: dict[str, tuple[str, str]] = {
    "P01": (
        "SELECT count(*) AS n FROM pb_deals WHERE is_venture_round",
        "The contract defines 'funding' as venture rounds. The v1 reference "
        "counted all 1,988 deals including share repurchases and M&A, "
        "contradicting rule 1. The system answered 1,240 and was right.",
    ),
    "P03": (
        "SELECT count(DISTINCT company_id) AS n FROM pb_deals WHERE is_venture_round",
        "Same definition: 'companies with funding data' means companies with a "
        "venture round. The v1 reference counted companies with any deal (359). "
        "The system asked which was meant — reasonable against v1's contract, "
        "now settled by the sharpened definition.",
    ),
    "P05": (
        "SELECT round(sum(d.deal_size_usd_m), 1) AS venture_usd_m FROM pb_deals d "
        "JOIN pb_companies c USING (company_id) "
        f"WHERE {CO.format(n='Aidoc')} AND d.is_venture_round",
        "Rule 11 requires one decimal place. The v1 reference rounded to two "
        "(420.26), violating the contract it was written against. The system "
        "returned 420.3 and was right.",
    ),
    "P12": (
        "SELECT d.deal_date, d.deal_type, d.deal_size_usd_m FROM pb_deals d "
        "JOIN pb_companies c USING (company_id) "
        f"WHERE {CO.format(n='Butterfly Network')} AND d.is_venture_round "
        "ORDER BY d.deal_date",
        "Unchanged SQL, but the contract now defines a default listing projection "
        "(new rule 14). v1 had no such rule, so the column choice was "
        "unspecified and the system added deal_size_status defensibly.",
    ),
    "P13": (
        "SELECT hq_country, count(*) AS n FROM pb_companies GROUP BY 1 "
        "ORDER BY n DESC, hq_country",
        "An enumeration, not a ranking, so rule 8's top-10 default does not "
        "apply. The v1 reference capped at 10 arbitrarily. Same defect as G01 in "
        "the FDA set.",
    ),
}


def main() -> int:
    v1 = yaml.safe_load(V1.read_text())
    out, changed = [], []

    with connect() as con:
        for case in v1["cases"]:
            entry = dict(case)
            cid = entry["id"]
            if cid in CORRECTIONS:
                sql, reason = CORRECTIONS[cid]
                entry["reference_sql"] = sql
                entry["changed_in_v2"] = reason
                changed.append(cid)
            if entry["expects"] == "answer":
                df = con.execute(entry["reference_sql"]).df()
                entry["expected_answer"] = {
                    "row_count": len(df),
                    "columns": list(df.columns),
                    "rows": [{k: normalize_cell(v) for k, v in r.items()}
                             for r in df.head(12).to_dict("records")],
                    "truncated": len(df) > 12,
                }
            out.append(entry)

    doc = {
        "schema_version": "pb-2.0.0",
        "source": "pitchbook",
        "supersedes": "golden_pitchbook.yaml",
        "frozen": False,
        "note": ("Draft. pitchbook-v1 remains frozen. Five references corrected "
                 "against stated contract rules; questions byte-identical to v1."),
        "cases": out,
    }
    path = Path(__file__).parent / "golden_pitchbook_v2.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))
    print(f"wrote {path} — {len(out)} cases, corrected: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
