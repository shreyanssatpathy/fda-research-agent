"""Streamlit interface for the FDA AI-device research copilot.

Rendering only — all behaviour lives in `fda_agent.answer`, so it can be tested
without a browser.

The central UI decision: a refusal is not an empty table. Refusals, clarifying
questions, and zero-row results are rendered as three visibly different things,
because collapsing them is how a user reads "no 2026 clearances" off a system
that simply has no 2026 data.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st  # noqa: E402

from fda_agent.config import DB_PATH, MAX_ROWS  # noqa: E402
from fda_agent.db import connect  # noqa: E402
import pandas as pd  # noqa: E402

from fda_agent.display import for_display  # noqa: E402
from fda_agent.llm.budget import Budget  # noqa: E402
from fda_agent.research import research  # noqa: E402

st.set_page_config(page_title="FDA AI Device Copilot", page_icon="🔬", layout="wide")

# Chosen to span every route, so the router is visible rather than implied.
EXAMPLES = [
    "How many AI devices were cleared annually since 2015?",       # fda
    "How much venture funding has Aidoc raised?",                  # pitchbook
    "What is the median capital raised before first clearance?",   # timeline
    "Which companies raised under $50m before their first clearance?",
    "Tell me about Aidoc.",                                        # profile
    "Which AI devices were later recalled?",                       # refuse
]

ROUTE_LABEL = {
    "fda": "FDA clearances",
    "pitchbook": "Company funding",
    "timeline": "Funding vs. first clearance",
    "profile": "Company profile — both sources",
    "refuse": "Out of scope",
}


@st.cache_data(show_spinner=False)
def coverage() -> dict:
    with connect() as con:
        row = con.execute(
            "SELECT count(*), count(DISTINCT company_name), "
            "min(decision_date), max(decision_date) FROM fda_510k"
        ).fetchone()
        deals, funded = con.execute(
            "SELECT count(*), count(DISTINCT company_id) FROM pb_deals "
            "WHERE is_venture_round"
        ).fetchone()
        with_funding = con.execute(
            "SELECT count(*) FROM company_funding_timeline WHERE rounds_before > 0"
        ).fetchone()[0]
    return {"rows": row[0], "companies": row[1], "start": row[2], "end": row[3],
            "deals": deals, "funded": funded, "with_funding": with_funding}


def render_scope(cov: dict) -> None:
    st.caption(
        f"**{cov['rows']:,} AI-enabled 510(k) clearances** from "
        f"{cov['companies']:,} companies ({cov['start']} to {cov['end']}), joined to "
        f"**{cov['deals']:,} venture rounds** across {cov['funded']:,} companies. "
        f"{cov['with_funding']:,} companies have funding recorded before their first "
        "clearance. FDA data is the AI/ML-Enabled Device List, not all clearances."
    )


def render_answer(a) -> None:
    if a.outcome == "refused":
        st.warning(f"**Not answerable from this data.**\n\n{a.message}", icon="⚠️")
        st.caption(
            "This is a refusal, not a result of zero. The system declines rather "
            "than returning a number the data cannot support."
        )
        return

    if a.outcome == "clarify":
        st.info(f"**Need one clarification.**\n\n{a.message}", icon="❓")
        return

    if a.outcome == "blocked":
        st.error(f"**Query blocked by the safety layer.**\n\n{a.message}", icon="🛑")
        return

    if a.outcome == "unavailable":
        st.warning(f"**Temporarily unreachable.**\n\n{a.message}", icon="⏳")
        return

    if a.outcome == "error":
        st.error(a.message, icon="🚨")
        return

    if a.outcome == "empty":
        st.info(
            "**The query ran and matched no rows.** That is a real result — it is "
            "not a refusal, and not an error.",
            icon="📭",
        )
        st.code(a.executed_sql, language="sql")
        return

    if a.message:
        st.markdown(a.message)

    st.dataframe(for_display(a.rows), width="stretch", hide_index=True)

    bits = [f"{len(a.rows):,} row(s)"]
    if a.duration_ms is not None:
        bits.append(f"{a.duration_ms} ms")
    # Only say "capped" when the cap actually bit. The guard adds a LIMIT to every
    # query, so limit_applied alone would claim truncation on a one-row result.
    if len(a.rows) >= MAX_ROWS:
        bits.append(f"truncated at {MAX_ROWS:,} rows")
    st.caption(" · ".join(bits))

    st.download_button(
        "Download CSV",
        a.rows.to_csv(index=False).encode(),
        file_name="fda_query_result.csv",
        mime="text/csv",
    )


def render_profile(profile) -> None:
    """Render assembled evidence. Not a query result — a company dossier."""
    ev = profile.evidence
    clearances = ev.of_type("FDA_CLEARANCE")
    rounds = ev.of_type("FUNDING_ROUND")

    c1, c2, c3 = st.columns(3)
    c1.metric("AI 510(k) clearances", len(clearances))
    c2.metric("Venture rounds", len(rounds))
    if profile.capital_before_first_clearance_usd_m is not None:
        c3.metric(
            "Raised before 1st clearance",
            f"\\${profile.capital_before_first_clearance_usd_m:,.1f}m",
            help=f"{profile.rounds_before_first_clearance} round(s) before "
                 f"{profile.first_clearance}",
        )
    else:
        c3.metric("Raised before 1st clearance", "—")

    if clearances:
        st.markdown("**FDA clearances**")
        st.dataframe(
            for_display(pd.DataFrame([
                {"regnumber": f.source_id, "decision_date": f.date,
                 "device": f.data["device_trade_name"],
                 "product_code": f.data["product_code"],
                 "specialty": f.data["medical_specialty"]}
                for f in clearances
            ])),
            width="stretch", hide_index=True,
        )

    if rounds:
        st.markdown("**Venture funding**")
        st.dataframe(
            for_display(pd.DataFrame([
                {"deal_id": f.source_id, "deal_date": f.date,
                 "deal_type": f.data["deal_type"],
                 "size_usd_m": f.data["deal_size_usd_m"],
                 "size_status": f.data["size_status"]}
                for f in rounds
            ])),
            width="stretch", hide_index=True,
        )

    for gap in ev.gaps:
        st.info(f"**{gap.topic}** — {gap.reason}", icon="🔍")

    st.caption(
        "Every row above carries its source id — `regnumber` from FDA, `deal_id` "
        "from PitchBook. Funding and clearances are assembled side by side, never "
        "joined, so no figure is multiplied by the other's row count."
    )


def render_caveats(a) -> None:
    if a.caveats:
        st.markdown("**Caveats**")
        for c in a.caveats:
            st.markdown(f"- {c}")


def render_provenance(a) -> None:
    g = a.generation
    if g is None:
        return
    with st.expander("SQL and provenance"):
        if a.generated_sql:
            st.markdown("**Generated SQL**")
            st.code(a.generated_sql, language="sql")
        if a.executed_sql and a.executed_sql != a.generated_sql:
            st.markdown("**Executed SQL** — rewritten by the safety layer")
            st.code(a.executed_sql, language="sql")
        st.markdown(
            f"model `{g.model_id}` · prompt `{g.prompt_version}` · "
            f"schema `{g.schema_version}` · contract `{g.contract_hash}` · "
            + ("served from cache" if g.cached else f"\\${g.cost_usd:.4f}")
        )


def main() -> None:
    st.title("FDA AI Device Research Copilot")

    if not DB_PATH.exists():
        st.error(
            "No database found. Build it first:\n\n"
            "`PYTHONPATH=src python -m fda_agent.ingest`"
        )
        return

    cov = coverage()
    render_scope(cov)

    with st.sidebar:
        st.subheader("Sources")
        st.metric("AI 510(k) clearances", f"{cov['rows']:,}")
        st.metric("Companies", f"{cov['companies']:,}")
        st.metric("Venture rounds", f"{cov['deals']:,}")
        st.caption(
            f"FDA coverage ends **{cov['end']}**; later periods are refused. "
            f"Funding is recorded for {cov['funded']:,} of {cov['companies']:,} "
            "companies — for the rest it is **missing, not zero**."
        )

        st.subheader("Spend")
        b = Budget()
        st.progress(min(1.0, b.spent_usd / b.ceiling_usd))
        # Escaped: Streamlit markdown treats $...$ as LaTeX and eats the signs.
        st.caption(f"\\${b.spent_usd:.4f} of \\${b.ceiling_usd:.2f} ceiling")

        st.subheader("What it will not do")
        st.caption(
            "Recalls, PMA approvals, clearance rates, share-of-total, named "
            "investors, revenue and clinical trials have no answer in any source "
            "here, and are refused rather than estimated."
        )
        st.caption(
            "Companies PitchBook classes as corporations — GE Healthcare, Siemens, "
            "Philips, Medtronic — have **no funding profile by design**. That is "
            "scope, not evidence that they raised nothing."
        )

    st.markdown("**Try one**")
    cols = st.columns(3)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 3].button(ex, key=f"ex{i}", width="stretch"):
            st.session_state.question = ex

    question = st.text_input(
        "Ask about AI device clearances, company funding, or both",
        key="question",
        placeholder="e.g. Which companies raised the most before their first clearance?",
    )

    if not question:
        return

    with st.spinner("Routing and answering..."):
        r = research(question)

    if r.routing:
        st.caption(
            f"Routed to **{ROUTE_LABEL.get(r.routing.route, r.routing.route)}** — "
            f"{r.routing.reason}"
        )

    if r.outcome == "refused" and r.answer is None:
        st.warning(f"**Not answerable from this data.**\n\n{r.message}", icon="⚠️")
        st.caption(
            "This is a refusal, not a result of zero. No available source covers "
            "the question."
        )
        return

    if r.outcome == "unavailable":
        st.warning(
            f"**The model is temporarily unreachable.**\n\n{r.message}", icon="⏳"
        )
        st.caption(
            "Nothing is wrong with the question or the data — this is an upstream "
            "outage. Ask again in a moment."
        )
        return

    if r.outcome == "clarify" and r.answer is None:
        st.info(f"**Need one clarification.**\n\n{r.message}", icon="❓")
        return

    if r.profile is not None and r.outcome == "answered":
        st.markdown(f"### {r.profile.evidence.entity_name}")
        render_profile(r.profile)
        return

    if r.answer is None:
        st.error(r.message or "Nothing to show.", icon="🚨")
        return

    render_answer(r.answer)
    render_caveats(r.answer)
    render_provenance(r.answer)


if __name__ == "__main__":
    main()
