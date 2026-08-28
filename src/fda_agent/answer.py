"""Question in, answer out — the layer the UI and any CLI both call.

Kept separate from the Streamlit app so the behaviour is testable without a
browser, and so the app file contains only rendering.

The outcome is an explicit enum-like string rather than "rows, possibly empty".
That distinction is the whole point: a refusal and a zero-row result look
identical if you only return a dataframe, and conflating them is how a system
tells someone there were no clearances in 2026 when in truth it has no 2026 data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from fda_agent.llm.budget import Budget, BudgetExceeded
from fda_agent.query import QueryTimeout, run as run_sql
from fda_agent.sql_guard import SqlValidationError
from fda_agent.prompts import get_source
from fda_agent.text_to_sql import Generation, MissingCredentials, generate

# answered  - SQL ran and returned rows
# empty     - SQL ran and matched nothing (a real answer, distinct from refused)
# refused   - the data cannot answer the question
# clarify   - the question is ambiguous; the system is asking back
# blocked   - generated SQL failed validation (a safety event worth surfacing)
# error     - execution or budget failure
OUTCOMES = ("answered", "empty", "refused", "clarify", "blocked", "error")


@dataclass
class Answer:
    question: str
    outcome: str
    message: str = ""
    rows: pd.DataFrame | None = None
    generated_sql: str | None = None
    executed_sql: str | None = None
    caveats: list[str] = field(default_factory=list)
    generation: Generation | None = None
    duration_ms: int | None = None
    limit_applied: bool = False
    source: str = "fda"

    @property
    def is_declined(self) -> bool:
        return self.outcome in ("refused", "clarify")


def answer(
    question: str, *, budget: Budget | None = None, source: str = "fda"
) -> Answer:
    """Answer one question end to end. Never raises for expected failures.

    `source` selects which contract the model is given and which tables the guard
    permits. The two are kept in lockstep: a model told about `pb_deals` is also
    allowed to query it, and a model told about FDA data cannot reach PitchBook
    tables even if it writes SQL for them.
    """
    question = (question or "").strip()
    if not question:
        return Answer(question, "error", "Ask a question first.")

    try:
        gen = generate(question, budget=budget or Budget(), source=source)
    except MissingCredentials as err:
        return Answer(question, "error", str(err))
    except BudgetExceeded as err:
        return Answer(question, "error", f"Spend ceiling reached. {err}")

    base = dict(
        question=question,
        generated_sql=gen.decision.sql,
        caveats=gen.decision.caveats,
        generation=gen,
        source=source,
    )

    if gen.decision.action == "refuse":
        return Answer(outcome="refused", message=gen.decision.explanation, **base)
    if gen.decision.action == "clarify":
        return Answer(outcome="clarify", message=gen.decision.explanation, **base)

    try:
        result = run_sql(
            gen.decision.sql, question=question, allowlist=get_source(source).tables
        )
    except SqlValidationError as err:
        return Answer(
            outcome="blocked",
            message=f"The generated query was rejected by the safety layer: {err}",
            **base,
        )
    except QueryTimeout as err:
        return Answer(outcome="error", message=str(err), **base)
    except Exception as err:  # noqa: BLE001 - surface, never swallow
        return Answer(outcome="error", message=f"Query failed: {err}", **base)

    return Answer(
        outcome="empty" if result.rows.empty else "answered",
        message=gen.decision.explanation,
        rows=result.rows,
        executed_sql=result.validated.sql,
        duration_ms=result.duration_ms,
        limit_applied=result.validated.limit_applied,
        **base,
    )
