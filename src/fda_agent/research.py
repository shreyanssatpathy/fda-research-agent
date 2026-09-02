"""The single entry point: question in, sourced answer out.

Routes the question, dispatches to the handler that route names, and returns a
uniform result. Two kinds of handler:

- **SQL routes** (`fda`, `pitchbook`, `timeline`) generate scoped SQL against one
  source. The guard's per-source allowlist means a route is a hard boundary, not
  a suggestion.
- **The `profile` route** assembles evidence deterministically. "Tell me about
  Aidoc" is not a SQL question — it wants every fact about one entity, which is
  assembly rather than a query.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from fda_agent.answer import Answer, answer
from fda_agent.compose import Profile, company_profile
from fda_agent.llm.budget import Budget, BudgetExceeded, LedgerCorrupted
from fda_agent.llm.errors import LLMUnavailable
from fda_agent.router import Routing, route_question
from fda_agent.text_to_sql import MissingCredentials


@dataclass
class Research:
    question: str
    routing: Routing | None = None
    answer: Answer | None = None
    profile: Profile | None = None
    outcome: str = "error"
    message: str = ""

    @property
    def rows(self) -> pd.DataFrame | None:
        return self.answer.rows if self.answer else None

    @property
    def source(self) -> str | None:
        return self.routing.route if self.routing else None


def research(question: str, *, budget: Budget | None = None) -> Research:
    """Answer any question, choosing the source. Never raises for expected failures."""
    question = (question or "").strip()
    if not question:
        return Research(question, outcome="error", message="Ask a question first.")

    budget = budget or Budget()
    try:
        routing = route_question(question, budget=budget)
    except MissingCredentials as err:
        return Research(question, outcome="error", message=str(err))
    except BudgetExceeded as err:
        return Research(question, outcome="error", message=f"Spend ceiling reached. {err}")
    except LedgerCorrupted as err:
        return Research(question, outcome="error", message=str(err))
    except LLMUnavailable as err:
        # An outage upstream is not a crash in this application.
        return Research(
            question,
            outcome="unavailable" if err.retryable else "error",
            message=str(err),
        )

    if routing.route == "refuse":
        return Research(question, routing=routing, outcome="refused", message=routing.reason)

    if routing.route == "profile":
        name = routing.company or question
        profile = company_profile(name)
        if profile.is_unknown:
            return Research(
                question, routing=routing, profile=profile, outcome="refused",
                message=f"No company matching {name!r} appears in the FDA data.",
            )
        if profile.is_ambiguous:
            names = ", ".join(m.company_name for m in profile.resolution.matches)
            return Research(
                question, routing=routing, profile=profile, outcome="clarify",
                message=f"{name!r} matches more than one company: {names}. Which did you mean?",
            )
        ev = profile.evidence
        return Research(
            question, routing=routing, profile=profile, outcome="answered",
            message=(
                f"{ev.entity_name}: {len(ev.of_type('FDA_CLEARANCE'))} AI 510(k) "
                f"clearances, {len(ev.of_type('FUNDING_ROUND'))} venture rounds."
            ),
        )

    a = answer(question, budget=budget, source=routing.route)
    return Research(
        question, routing=routing, answer=a, outcome=a.outcome, message=a.message
    )
