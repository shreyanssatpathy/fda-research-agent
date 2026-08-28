"""Author the v4 golden eval set.

Unlike v2 and v3, **no reference SQL changes here.** Every question and every
query is identical to v3. What changed is the data: the PitchBook bridge merged
13 FDA company-name variants that are provably one company each, so
`count(DISTINCT company_name)` and every company ranking moved.

That makes v4 a re-execution, not a re-authoring — the cheapest and least
suspicious kind of eval update. The five affected cases are C02, M02, M04, M05
and F03, all company-cardinality questions.

v1, v2 and v3 stay frozen. Their expected answers describe the data as it was
before entity resolution, which is why they are kept rather than edited.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from fda_agent.db import connect  # noqa: E402
from normalize import normalize_cell  # noqa: E402

V3 = Path(__file__).parent / "golden_v3.yaml"


def main() -> int:
    v3 = yaml.safe_load(V3.read_text())
    out, changed = [], []

    with connect() as con:
        for case in v3["cases"]:
            entry = {k: v for k, v in case.items()}
            if entry["expects"] != "answer":
                out.append(entry)
                continue

            df = con.execute(entry["reference_sql"]).df()
            new = {
                "row_count": len(df),
                "columns": list(df.columns),
                "rows": [
                    {k: normalize_cell(v) for k, v in r.items()}
                    for r in df.head(12).to_dict("records")
                ],
                "truncated": len(df) > 12,
            }
            if new != entry.get("expected_answer"):
                changed.append(entry["id"])
                entry["changed_in_v4"] = (
                    "Expected answer re-executed after PitchBook-bridge entity "
                    "resolution merged 13 company-name variants. Reference SQL "
                    "unchanged."
                )
            entry["expected_answer"] = new
            out.append(entry)

    doc = {
        "schema_version": "4.0.0",
        "supersedes": "golden_v3.yaml",
        "frozen": False,
        "note": (
            "Draft. v1-v3 remain frozen. No reference SQL changed; expected answers "
            "were re-executed after entity resolution merged 13 company-name "
            "variants. Questions are byte-identical to v1."
        ),
        "cases": out,
    }
    path = Path(__file__).parent / "golden_v4.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))
    print(f"wrote {path} — {len(out)} cases")
    print(f"expected answers that moved: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
