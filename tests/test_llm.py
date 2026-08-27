"""Tests for the budget ceiling, the response cache, and generation wiring.

All offline. No API key is required and no request is made — the LLM client is
stubbed, so these run in CI and cost nothing.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fda_agent.llm.budget import (
    Budget,
    BudgetExceeded,
    Usage,
    cost_usd,
)
from fda_agent.llm.cache import ResponseCache, cache_key
from fda_agent.text_to_sql import SqlDecision, generate


# --- budget ----------------------------------------------------------------------


def test_cost_matches_published_rates():
    """1M in + 1M out on Opus 5 is $5 + $25."""
    c = cost_usd(Usage(input_tokens=1_000_000, output_tokens=1_000_000), "claude-opus-5")
    assert c == pytest.approx(30.00)


def test_cache_reads_are_cheaper_than_fresh_input():
    fresh = cost_usd(Usage(input_tokens=100_000, output_tokens=0), "claude-opus-5")
    cached = cost_usd(
        Usage(input_tokens=0, output_tokens=0, cache_read_tokens=100_000),
        "claude-opus-5",
    )
    assert cached == pytest.approx(fresh * 0.1)


def test_unknown_model_raises_rather_than_costing_nothing():
    """Silently pricing an unknown model at zero would defeat the ceiling."""
    with pytest.raises(ValueError, match="no pricing recorded"):
        cost_usd(Usage(1000, 1000), "some-future-model")


def test_ceiling_blocks_before_spending(tmp_path):
    b = Budget(ceiling_usd=1.00, ledger_path=tmp_path / "l.json")
    b.record(Usage(input_tokens=200_000, output_tokens=0), "claude-opus-5")  # $1.00
    with pytest.raises(BudgetExceeded):
        b.check()


def test_ceiling_blocks_a_call_that_would_cross_it(tmp_path):
    b = Budget(ceiling_usd=1.00, ledger_path=tmp_path / "l.json")
    b.record(Usage(input_tokens=100_000, output_tokens=0), "claude-opus-5")  # $0.50
    b.check(estimated_usd=0.10)  # fine
    with pytest.raises(BudgetExceeded, match="would exceed"):
        b.check(estimated_usd=0.90)


def test_ledger_survives_a_new_process(tmp_path):
    """A crashed run must not get a fresh budget on the next attempt."""
    path = tmp_path / "l.json"
    Budget(ceiling_usd=5.0, ledger_path=path).record(
        Usage(input_tokens=100_000, output_tokens=0), "claude-opus-5"
    )
    reloaded = Budget(ceiling_usd=5.0, ledger_path=path)
    assert reloaded.spent_usd == pytest.approx(0.50)
    assert reloaded.remaining_usd == pytest.approx(4.50)
    assert json.loads(path.read_text())["calls"] == 1


# --- cache -----------------------------------------------------------------------


def test_cache_key_ignores_dict_ordering():
    """Unsorted keys would silently miss and make every run pay full price."""
    assert cache_key({"a": 1, "b": 2}) == cache_key({"b": 2, "a": 1})


def test_cache_key_changes_with_content():
    assert cache_key({"q": "one"}) != cache_key({"q": "two"})


def test_cache_roundtrip(tmp_path):
    c = ResponseCache(cache_dir=tmp_path)
    assert c.get("abc123") is None
    c.put("abc123", {"action": "refuse"}, meta={"question": "q"})
    assert c.get("abc123") == {"action": "refuse"}


# --- generation ------------------------------------------------------------------


class StubClient:
    """Minimal stand-in for anthropic.Anthropic, recording call count."""

    def __init__(self, decision: SqlDecision):
        self.decision = decision
        self.calls = 0
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return SimpleNamespace(
            parsed_output=self.decision,
            usage=SimpleNamespace(
                input_tokens=2000,
                output_tokens=200,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def _stub(action="sql", sql="SELECT count(*) FROM fda_510k"):
    return StubClient(SqlDecision(action=action, sql=sql, explanation="x"))


def test_generation_carries_required_provenance(tmp_path):
    g = generate(
        "How many clearances?",
        client=_stub(),
        cache=ResponseCache(tmp_path / "c"),
        budget=Budget(ledger_path=tmp_path / "l.json"),
    )
    assert g.model_id == "claude-opus-5"
    assert g.prompt_version == "text_to_sql/v1"
    assert g.schema_version
    assert g.contract_hash
    assert not g.cached
    assert g.cost_usd > 0


def test_second_identical_question_is_cached_and_free(tmp_path):
    client = _stub()
    cache = ResponseCache(tmp_path / "c")
    budget = Budget(ledger_path=tmp_path / "l.json")

    first = generate("How many clearances?", client=client, cache=cache, budget=budget)
    spent_after_first = budget.spent_usd
    second = generate("How many clearances?", client=client, cache=cache, budget=budget)

    assert client.calls == 1, "cache hit must not reach the API"
    assert not first.cached and second.cached
    assert second.cost_usd == 0
    assert budget.spent_usd == spent_after_first, "a cache hit must not consume budget"


def test_budget_is_checked_before_the_api_is_called(tmp_path):
    """No request should be made once the ceiling is reached."""
    client = _stub()
    budget = Budget(ceiling_usd=0.001, ledger_path=tmp_path / "l.json")
    budget.record(Usage(input_tokens=100_000, output_tokens=0), "claude-opus-5")
    with pytest.raises(BudgetExceeded):
        generate("q", client=client, cache=ResponseCache(tmp_path / "c"), budget=budget)
    assert client.calls == 0


def test_contract_is_sent_as_a_cacheable_system_prefix(tmp_path):
    client = _stub()
    generate("q", client=client, cache=ResponseCache(tmp_path / "c"),
             budget=Budget(ledger_path=tmp_path / "l.json"))
    system = client.last_kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "Rules the generated SQL must follow" in system[0]["text"]
    assert client.last_kwargs["messages"][0]["content"] == "q"


def test_refusal_decision_carries_no_sql(tmp_path):
    client = StubClient(
        SqlDecision(action="refuse", sql=None, explanation="Data ends in 2025.")
    )
    g = generate("How many in 2026?", client=client, cache=ResponseCache(tmp_path / "c"),
                 budget=Budget(ledger_path=tmp_path / "l.json"))
    assert g.decision.action == "refuse"
    assert g.decision.sql is None


# --- .env loading ------------------------------------------------------------------


def test_env_file_sets_unset_variables(tmp_path, monkeypatch):
    from fda_agent.env import load_env

    monkeypatch.delenv("SOME_TEST_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text('# comment\n\nSOME_TEST_KEY="value-1"\nexport OTHER_KEY=value-2\n')
    assert set(load_env(p)) == {"SOME_TEST_KEY", "OTHER_KEY"}
    import os

    assert os.environ["SOME_TEST_KEY"] == "value-1"
    assert os.environ["OTHER_KEY"] == "value-2"


def test_env_file_does_not_override_an_explicit_export(tmp_path, monkeypatch):
    """A shell export must win over a stale file, or debugging becomes guesswork."""
    from fda_agent.env import load_env

    monkeypatch.setenv("SOME_TEST_KEY", "from-shell")
    p = tmp_path / ".env"
    p.write_text("SOME_TEST_KEY=from-file\n")
    assert load_env(p) == []
    import os

    assert os.environ["SOME_TEST_KEY"] == "from-shell"


def test_missing_env_file_is_not_an_error(tmp_path):
    from fda_agent.env import load_env

    assert load_env(tmp_path / "nope.env") == []
