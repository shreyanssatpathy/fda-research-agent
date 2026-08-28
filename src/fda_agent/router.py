"""Question router.

The one place the model is asked *what kind of question this is* rather than to
answer it. It picks a route; the route decides which contract and which tables
come next. The model never chooses tables directly and never writes a
cross-source join — those are properties of the route, enforced by the guard's
per-source allowlist.

Routes:

| route | handled by | tables |
|---|---|---|
| `fda` | text-to-SQL | `fda_510k` |
| `pitchbook` | text-to-SQL | `pb_deals`, `pb_companies` |
| `timeline` | text-to-SQL | `company_funding_timeline` |
| `profile` | deterministic composition | both, joined in code |
| `refuse` | — | none |

`profile` exists because "tell me about company X" is not a SQL question. It wants
every fact about one entity from every source, which is assembly, not a query.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from fda_agent.config import MODEL_ID
from fda_agent.llm.budget import Budget
from fda_agent.llm.cache import ResponseCache, cache_key

ROUTER_VERSION = "router/v1"

_INSTRUCTIONS = """\
You route a question to the one source that can answer it. You do not answer it.

`fda` — FDA 510(k) clearances of AI-enabled devices, 2010 to 2025.
  Device names, product codes, medical specialties, submission types, clearance
  dates, company clearance counts, country of filing.
  "How many AI devices were cleared in 2023?" · "Show Aidoc's clearances."

`pitchbook` — company funding: venture rounds, deal types, sizes, dates, and
  company attributes like founding year, HQ and financing status.
  "How much has Aidoc raised?" · "What is the median venture round size?"

`timeline` — one row per company combining both: first clearance date, total
  clearances, and venture funding split before and after that date.
  Use this for any question relating funding to clearance timing across companies.
  "Median capital raised before first clearance" ·
  "Which companies raised under $50m before clearing?"

`profile` — everything known about ONE named company, from every source.
  Use when the question asks for a company's history or overview rather than a
  specific figure.
  "Tell me about Aidoc." · "What is Viz.ai's story?"
  Set `company` to the name as the user wrote it.

`refuse` — no source can answer it. Recalls, PMA approvals, investors by name,
  revenue, clinical trials, or anything outside AI 510(k) devices and their
  funding. Say what is missing.

Prefer the narrowest route that fully answers the question. If a question needs
both funding and clearance timing, that is `timeline`, not two routes.
"""


class Route(BaseModel):
    route: Literal["fda", "pitchbook", "timeline", "profile", "refuse"] = Field(
        description="Which source answers this question"
    )
    company: str | None = Field(
        default=None, description="For `profile`, the company name as written."
    )
    reason: str = Field(description="One sentence explaining the choice.")


@dataclass(frozen=True)
class Routing:
    question: str
    route: str
    company: str | None
    reason: str
    cached: bool
    cost_usd: float = 0.0


def route_question(
    question: str,
    *,
    client=None,
    cache: ResponseCache | None = None,
    budget: Budget | None = None,
    use_cache: bool = True,
) -> Routing:
    """Classify one question. Cache first, budget second, API last."""
    cache = cache or ResponseCache()
    budget = budget or Budget()

    payload = {
        "model": MODEL_ID,
        "router_version": ROUTER_VERSION,
        "instructions": _INSTRUCTIONS,
        "question": question,
        "schema": Route.model_json_schema(),
    }
    key = cache_key(payload)

    if use_cache and (hit := cache.get(key)) is not None:
        r = Route.model_validate(hit)
        return Routing(question, r.route, r.company, r.reason, cached=True)

    budget.check()
    if client is None:
        from fda_agent.text_to_sql import _default_client

        client = _default_client()

    from fda_agent.llm.budget import Usage

    response = client.messages.parse(
        model=MODEL_ID,
        max_tokens=1024,
        system=[{"type": "text", "text": _INSTRUCTIONS,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": question}],
        output_format=Route,
    )
    decision = response.parsed_output
    cost = budget.record(
        Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        ),
        MODEL_ID,
    )
    cache.put(key, decision.model_dump(),
              meta={"router_version": ROUTER_VERSION, "question": question})
    return Routing(question, decision.route, decision.company, decision.reason,
                   cached=False, cost_usd=cost)
