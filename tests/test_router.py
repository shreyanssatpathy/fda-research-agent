"""Tests for the router and the research entry point.

Offline — the classifier is stubbed. What is tested is dispatch and the
boundaries between routes, not the model's judgement.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fda_agent.config import DB_PATH
from fda_agent.db import connect
from fda_agent.prompts import SOURCES, get_source
from fda_agent.router import Route, Routing
from fda_agent.sql_guard import SqlValidationError, validate


def _has(table: str) -> bool:
    if not DB_PATH.exists():
        return False
    with connect() as con:
        return bool(
            con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                [table],
            ).fetchone()[0]
        )


pytestmark = pytest.mark.skipif(
    not _has("company_funding_timeline"), reason="cross-source table not built"
)


# --- routes are hard boundaries, not suggestions -----------------------------------


def test_every_route_has_a_disjoint_table_set():
    """No two SQL sources share a table, so a route change really changes scope."""
    sql_sources = [SOURCES[n] for n in ("fda", "pitchbook", "timeline")]
    seen: set[str] = set()
    for s in sql_sources:
        assert not (s.tables & seen), f"{s.name} overlaps an earlier source"
        seen |= s.tables


@pytest.mark.parametrize(
    "source,forbidden",
    [
        ("fda", "SELECT * FROM company_funding_timeline"),
        ("fda", "SELECT * FROM pb_deals"),
        ("pitchbook", "SELECT * FROM fda_510k"),
        ("pitchbook", "SELECT * FROM company_funding_timeline"),
        ("timeline", "SELECT * FROM fda_510k"),
        ("timeline", "SELECT * FROM pb_deals"),
    ],
)
def test_a_route_cannot_reach_another_routes_tables(source, forbidden):
    with pytest.raises(SqlValidationError, match="not on the allowlist"):
        validate(forbidden, allowlist=get_source(source).tables)


def test_timeline_route_cannot_rejoin_its_own_inputs():
    """Re-joining the timeline to fda_510k would reintroduce the fan-out the
    table exists to remove."""
    sql = (
        "SELECT * FROM company_funding_timeline t "
        "JOIN fda_510k f ON f.company_name = t.company_name"
    )
    with pytest.raises(SqlValidationError):
        validate(sql, allowlist=get_source("timeline").tables)


def test_each_route_can_query_its_own_table():
    for name, sql in (
        ("fda", "SELECT count(*) FROM fda_510k"),
        ("pitchbook", "SELECT count(*) FROM pb_deals"),
        ("timeline", "SELECT count(*) FROM company_funding_timeline"),
    ):
        assert validate(sql, allowlist=get_source(name).tables).sql


# --- dispatch -----------------------------------------------------------------------


def _stub_route(monkeypatch, route: str, company: str | None = None):
    monkeypatch.setattr(
        "fda_agent.research.route_question",
        lambda q, **kw: Routing(q, route, company, "stubbed", cached=True),
    )


def test_profile_route_assembles_evidence_without_sql(monkeypatch):
    from fda_agent.research import research

    _stub_route(monkeypatch, "profile", "Aidoc")
    r = research("Tell me about Aidoc")
    assert r.outcome == "answered"
    assert r.answer is None, "profile route must not run generated SQL"
    assert r.profile is not None
    assert len(r.profile.evidence.of_type("FDA_CLEARANCE")) == 31


def test_profile_route_clarifies_on_an_ambiguous_company(monkeypatch):
    from fda_agent.research import research

    _stub_route(monkeypatch, "profile", "Samsung")
    r = research("Tell me about Samsung")
    assert r.outcome == "clarify"
    assert "more than one company" in r.message


def test_profile_route_refuses_an_unknown_company(monkeypatch):
    from fda_agent.research import research

    _stub_route(monkeypatch, "profile", "No Such Company")
    r = research("Tell me about No Such Company")
    assert r.outcome == "refused"


def test_refuse_route_short_circuits_before_any_sql(monkeypatch):
    from fda_agent.research import research

    _stub_route(monkeypatch, "refuse")
    r = research("Which devices were recalled?")
    assert r.outcome == "refused"
    assert r.answer is None and r.profile is None


def test_blank_question_never_reaches_the_router():
    from fda_agent.research import research

    r = research("   ")
    assert r.outcome == "error"
    assert r.routing is None


# --- the router contract ------------------------------------------------------------


def test_route_schema_lists_exactly_the_known_routes():
    allowed = Route.model_json_schema()["properties"]["route"]["enum"]
    assert set(allowed) == {"fda", "pitchbook", "timeline", "profile", "refuse"}


def test_router_is_cached_and_free_on_repeat(tmp_path):
    from fda_agent.llm.budget import Budget
    from fda_agent.llm.cache import ResponseCache
    from fda_agent.router import route_question

    calls = {"n": 0}

    class Stub:
        def __init__(self):
            self.messages = SimpleNamespace(parse=self._parse)

        def _parse(self, **kw):
            calls["n"] += 1
            return SimpleNamespace(
                parsed_output=Route(route="fda", company=None, reason="r"),
                usage=SimpleNamespace(
                    input_tokens=500, output_tokens=50,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0,
                ),
            )

    cache, budget = ResponseCache(tmp_path / "c"), Budget(ledger_path=tmp_path / "l.json")
    first = route_question("q", client=Stub(), cache=cache, budget=budget)
    spent = budget.spent_usd
    second = route_question("q", client=Stub(), cache=cache, budget=budget)

    assert calls["n"] == 1
    assert not first.cached and second.cached
    assert budget.spent_usd == spent
