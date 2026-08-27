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

from fda_agent.answer import answer  # noqa: E402
from fda_agent.config import DB_PATH, MAX_ROWS  # noqa: E402
from fda_agent.db import connect  # noqa: E402
from fda_agent.display import for_display  # noqa: E402
from fda_agent.llm.budget import Budget  # noqa: E402

st.set_page_config(page_title="FDA AI Device Copilot", page_icon="🔬", layout="wide")

EXAMPLES = [
    "How many AI devices were cleared annually since 2015?",
    "Which companies have the most AI device clearances?",
    "When did Viz.ai receive its first AI clearance?",
    "Show all FDA clearances for Aidoc.",
    "How many AI devices were cleared in 2026?",
    "What percentage of all FDA clearances are AI-enabled?",
]


@st.cache_data(show_spinner=False)
def coverage() -> dict:
    with connect() as con:
        row = con.execute(
            "SELECT count(*), count(DISTINCT company_name), "
            "min(decision_date), max(decision_date) FROM fda_510k"
        ).fetchone()
    return {"rows": row[0], "companies": row[1], "start": row[2], "end": row[3]}


def render_scope(cov: dict) -> None:
    st.caption(
        f"**{cov['rows']:,} AI-enabled 510(k) clearances** from "
        f"{cov['companies']:,} companies, {cov['start']} to {cov['end']}. "
        "This is the FDA's AI/ML-Enabled Device List — not all FDA clearances, and "
        "not this data's companies' non-AI devices."
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
        st.subheader("Scope")
        st.metric("Clearances", f"{cov['rows']:,}")
        st.metric("Companies", f"{cov['companies']:,}")
        st.caption(f"Coverage ends **{cov['end']}**. Questions about later periods are refused.")

        st.subheader("Spend")
        b = Budget()
        st.progress(min(1.0, b.spent_usd / b.ceiling_usd))
        # Escaped: Streamlit markdown treats $...$ as LaTeX and eats the signs.
        st.caption(f"\\${b.spent_usd:.4f} of \\${b.ceiling_usd:.2f} ceiling")

        st.subheader("What it will not do")
        st.caption(
            "Funding, recalls, PMA approvals, clearance rates, and share-of-total "
            "questions have no answer in this data and are refused rather than "
            "estimated."
        )

    st.markdown("**Try one**")
    cols = st.columns(3)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 3].button(ex, key=f"ex{i}", width="stretch"):
            st.session_state.question = ex

    question = st.text_input(
        "Ask about AI-enabled FDA device clearances",
        key="question",
        placeholder="e.g. Which companies received their first AI clearance in 2023?",
    )

    if not question:
        return

    with st.spinner("Generating and running SQL..."):
        a = answer(question)

    render_answer(a)
    render_caveats(a)
    render_provenance(a)


if __name__ == "__main__":
    main()
