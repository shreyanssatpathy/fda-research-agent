"""Question -> SQL, or a refusal, or a clarifying question.

Generation is only half the job. The layer must be able to decline: eleven of the
thirty-eight frozen eval cases have no correct SQL, and a system that always
produces a query will answer them confidently and wrongly. `Decision.action`
carries that choice explicitly rather than leaving it implied by the prose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from fda_agent.config import MODEL_ID, SCHEMA_VERSION
from fda_agent.llm.budget import Budget, BudgetExceeded, Usage
from fda_agent.llm.cache import ResponseCache, cache_key
from fda_agent.prompts import PROMPT_VERSION, build_system_prompt, contract_hash


class SqlDecision(BaseModel):
    """The structured response the model is constrained to return."""

    action: Literal["sql", "refuse", "clarify"] = Field(
        description="sql if answerable; refuse if the data cannot answer it; "
        "clarify if the question is ambiguous"
    )
    sql: str | None = Field(
        default=None, description="A single DuckDB SELECT. Null unless action is sql."
    )
    explanation: str = Field(
        description="One or two sentences. For refuse, say what is missing. "
        "For clarify, ask one specific question."
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Qualifications the SQL itself cannot express.",
    )


@dataclass(frozen=True)
class Generation:
    """A decision plus the provenance required by CLAUDE.md."""

    question: str
    decision: SqlDecision
    model_id: str
    prompt_version: str
    schema_version: str
    contract_hash: str
    cached: bool
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)


class MissingCredentials(Exception):
    """No Anthropic credentials are configured. Actionable, not a stack trace."""


def _has_credentials() -> bool:
    """Mirror the SDK's resolution order without constructing a client.

    The SDK constructs fine with no credentials and fails later, at request time,
    with a TypeError about headers. Checking up front turns that into one clear
    sentence at the point the user can act on it.
    """
    import os
    from pathlib import Path as _Path

    from fda_agent.env import load_env

    load_env()  # a .env in the repo root, if there is one; never overwrites exports

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if (_Path.home() / ".config" / "anthropic").exists():
        return True
    wif = ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID")
    return all(os.environ.get(k) for k in wif)


def _default_client():
    if not _has_credentials():
        raise MissingCredentials(
            "No Anthropic credentials found. Set one of:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  ant auth login   (stores a profile the SDK reads automatically)\n\n"
            "Only SQL generation needs this. The loader, the SQL guard and the "
            "eval scoring all run offline."
        )
    import os

    import anthropic

    # Identity-linked API keys must name the workspace the request acts in. The
    # SDK only reads ANTHROPIC_WORKSPACE_ID on the workload-identity path, so for
    # a plain API key the header has to be sent explicitly.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.Anthropic(default_headers=headers)


def _request_payload(question: str, system: str, effort: str) -> dict:
    """Everything that determines the answer, and nothing that does not.

    Timestamps and request ids are deliberately excluded — including them would
    make every key unique and silently disable the cache.
    """
    return {
        "model": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "contract_hash": contract_hash(),
        "effort": effort,
        "system": system,
        "question": question,
        "schema": SqlDecision.model_json_schema(),
    }


def generate(
    question: str,
    *,
    client=None,
    cache: ResponseCache | None = None,
    budget: Budget | None = None,
    effort: str = "medium",
    use_cache: bool = True,
) -> Generation:
    """Generate a decision for one question.

    Order matters: cache first, then budget, then the API. A cached question costs
    nothing and must not consume budget — otherwise re-running the eval set burns
    the ceiling without making a single call.
    """
    cache = cache or ResponseCache()
    budget = budget or Budget()
    system = build_system_prompt()
    payload = _request_payload(question, system, effort)
    key = cache_key(payload)

    if use_cache and (hit := cache.get(key)) is not None:
        return Generation(
            question=question,
            decision=SqlDecision.model_validate(hit),
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            contract_hash=payload["contract_hash"],
            cached=True,
        )

    budget.check()

    if client is None:
        client = _default_client()

    response = client.messages.parse(
        model=MODEL_ID,
        max_tokens=4096,
        system=[
            # Stable prefix: the contract does not change between questions, so
            # it is worth caching server-side. The question goes in messages,
            # after the breakpoint.
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        output_config={"effort": effort},
        messages=[{"role": "user", "content": question}],
        output_format=SqlDecision,
    )

    decision = response.parsed_output
    usage = Usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    )
    cost = budget.record(usage, MODEL_ID)

    cache.put(
        key,
        decision.model_dump(),
        meta={
            "model_id": MODEL_ID,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "contract_hash": payload["contract_hash"],
            "question": question,
        },
    )

    return Generation(
        question=question,
        decision=decision,
        model_id=MODEL_ID,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        contract_hash=payload["contract_hash"],
        cached=False,
        cost_usd=cost,
        usage=usage.__dict__,
    )
