"""Score the text-to-SQL layer against the frozen golden set.

Defaults to sample mode (CLAUDE.md); `--full` runs all 38 cases.

Scoring is deliberately strict about the thing that matters most: a case the
system was supposed to refuse but answered is counted as a failure, not partial
credit. Answering an unanswerable question confidently is the failure mode this
whole project is built to avoid.

Value comparison ignores column *names* — `n` versus `count` is a legitimate
stylistic difference — but not values, ordering, or row counts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from normalize import normalize_row  # noqa: E402

from fda_agent.llm.budget import Budget, BudgetExceeded  # noqa: E402
from fda_agent.query import run as run_sql  # noqa: E402
from fda_agent.sql_guard import SqlValidationError  # noqa: E402
from fda_agent.text_to_sql import MissingCredentials, generate  # noqa: E402

SETS = {
    "v1": Path(__file__).parent / "golden_v1.yaml",
    "v2": Path(__file__).parent / "golden_v2.yaml",
    "v3": Path(__file__).parent / "golden_v3.yaml",
}
SAMPLE_IDS = ["C01", "M03", "F01", "T01", "D01", "R01", "R03", "A01"]


def _values(rows: list[dict]) -> list[tuple]:
    return [normalize_row(r) for r in rows]


def score_answer(case: dict, result_rows) -> tuple[bool, str]:
    expected = case["expected_answer"]
    got = result_rows.to_dict("records")

    if len(got) != expected["row_count"]:
        return False, f"row count {len(got)} != expected {expected['row_count']}"

    exp_head = _values(expected["rows"])
    got_head = _values(got[: len(exp_head)])
    if exp_head != got_head:
        # Report the first row that actually differs. Reporting row 0 regardless
        # produced messages showing two identical tuples as a mismatch, which
        # sent debugging in the wrong direction.
        for i, (e, g) in enumerate(zip(exp_head, got_head)):
            if e != g:
                return False, f"row {i} differs: expected {e}, got {g}"
        return False, f"expected {len(exp_head)} comparable rows, got {len(got_head)}"
    return True, "match"


def evaluate_case(case: dict, *, budget: Budget) -> dict:
    out = {"id": case["id"], "category": case["category"], "question": case["question"]}
    try:
        gen = generate(case["question"], budget=budget)
    except BudgetExceeded as err:
        out.update(status="budget_exceeded", detail=str(err))
        return out

    out.update(action=gen.decision.action, cached=gen.cached, cost_usd=gen.cost_usd)

    expects = case["expects"]

    # v1 states one bucket and infers the action from the category; v2 states the
    # expected action directly, and allows `decline` where refuse and clarify are
    # both defensible.
    if expects == "refusal_or_clarification":
        expects = "clarify" if case["category"] == "clarify" else "refuse"

    if expects != "answer":
        if gen.decision.action == "sql":
            out.update(
                status="fail",
                detail="answered a question that has no correct answer",
                generated_sql=gen.decision.sql,
            )
        elif expects == "decline" or gen.decision.action == expects:
            out.update(status="pass", detail=gen.decision.explanation)
        else:
            out.update(
                status="partial",
                detail=f"declined but as {gen.decision.action!r}, expected {expects!r}",
            )
        return out

    if gen.decision.action != "sql":
        out.update(status="fail", detail=f"declined an answerable question: {gen.decision.explanation}")
        return out

    out["generated_sql"] = gen.decision.sql
    try:
        res = run_sql(gen.decision.sql, question=case["question"])
    except SqlValidationError as err:
        out.update(status="fail", detail=f"blocked by guard: {err}")
        return out
    except Exception as err:  # noqa: BLE001 - any execution error is a failure
        out.update(status="fail", detail=f"execution error: {err}")
        return out

    ok, detail = score_answer(case, res.rows)
    out.update(status="pass" if ok else "fail", detail=detail)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default="v3", choices=sorted(SETS), help="which golden set")
    ap.add_argument("--full", action="store_true", help="run all cases (default: sample)")
    ap.add_argument("--ceiling", type=float, default=None, help="override spend ceiling")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results.json")
    args = ap.parse_args(argv)

    golden = SETS[args.set]
    doc = yaml.safe_load(golden.read_text())
    print(f"golden set: {golden.name} (schema {doc['schema_version']})")
    if not doc.get("frozen"):
        print("refusing to run: golden set is not frozen", file=sys.stderr)
        return 2

    cases = doc["cases"]
    if not args.full:
        cases = [c for c in cases if c["id"] in SAMPLE_IDS]
        print(f"sample mode: {len(cases)} of {len(doc['cases'])} cases (--full for all)\n")

    budget = Budget(ceiling_usd=args.ceiling) if args.ceiling else Budget()
    print(f"budget: ${budget.spent_usd:.4f} spent of ${budget.ceiling_usd:.2f}\n")

    try:
        generate.__module__  # touch, then probe credentials on the first real call
    except Exception:  # pragma: no cover
        pass

    results = []
    for case in cases:
        try:
            r = evaluate_case(case, budget=budget)
        except MissingCredentials as err:
            print(f"\n{err}", file=sys.stderr)
            return 2
        results.append(r)
        mark = {"pass": "PASS", "fail": "FAIL", "partial": "PART"}.get(r["status"], "----")
        print(f"  {mark}  {r['id']}  {r['question'][:52]:54} {r.get('detail','')[:44]}")
        if r["status"] == "budget_exceeded":
            print("\nstopped: spend ceiling reached")
            break

    passed = sum(1 for r in results if r["status"] == "pass")
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["status"])

    print(f"\n{passed}/{len(results)} passed")
    for cat, statuses in sorted(by_cat.items()):
        print(f"  {cat:24} {statuses.count('pass')}/{len(statuses)}")

    refusal_failures = [
        r for r in results
        if r["category"].startswith(("refuse", "clarify")) and r["status"] == "fail"
    ]
    if refusal_failures:
        print(f"\n{len(refusal_failures)} unanswerable question(s) were answered anyway:")
        for r in refusal_failures:
            print(f"  {r['id']}: {r['question']}")

    args.out.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "golden_set": golden.name,
        "mode": "full" if args.full else "sample",
        "golden_schema_version": doc["schema_version"],
        "passed": passed,
        "total": len(results),
        "spend_usd": budget.spent_usd,
        "results": results,
    }, indent=2, default=str))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
