"""Streamlit demo UI for Munich RAG Compliance assistant."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

# ── Path setup so imports from src/ work when running from repo root ──────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.generation.generator import ComplianceGenerator
from src.retrieval.hybrid_search import retrieve_with_parents
from src.retrieval.vector_store import get_client

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Munich RAG Compliance",
    page_icon="⚖️",
    layout="wide",
)

# ── CSS — industrial/utilitarian dark theme ───────────────────────────────────

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
    background-color: #0d0f12;
    color: #e2e8f0;
}
h1, h2, h3 { font-family: 'IBM Plex Mono', monospace; letter-spacing: -0.02em; }
.stTextInput > div > div > input {
    background: #161a22; border: 1px solid #2d3748;
    color: #e2e8f0; font-family: 'IBM Plex Sans', sans-serif;
}
.stSelectbox > div > div {
    background: #161a22; border: 1px solid #2d3748; color: #e2e8f0;
}
.source-card {
    background: #161a22; border-left: 3px solid #3b82f6;
    padding: 0.75rem 1rem; margin-bottom: 0.75rem;
    border-radius: 0 4px 4px 0; font-size: 0.85rem;
}
.source-card code {
    font-family: 'IBM Plex Mono', monospace;
    color: #60a5fa; font-size: 0.8rem;
}
.answer-box {
    background: #111418; border: 1px solid #2d3748;
    padding: 1.25rem 1.5rem; border-radius: 6px;
    font-size: 0.97rem; line-height: 1.7;
}
.metric-row { display: flex; gap: 1.5rem; margin-bottom: 1rem; }
.metric { background: #161a22; padding: 0.5rem 1rem; border-radius: 4px;
          font-family: 'IBM Plex Mono', monospace; font-size: 0.8rem; }
.metric span { color: #3b82f6; font-weight: 600; }
</style>
""",
    unsafe_allow_html=True,
)

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("# ⚖️ Munich RAG Compliance")
st.markdown(
    "<p style='color:#94a3b8;font-size:0.9rem'>"
    "DSGVO · BDSG · Compliance-aware retrieval-augmented generation"
    "</p>",
    unsafe_allow_html=True,
)
st.divider()

# ── Session state ─────────────────────────────────────────────────────────────

# st.session_state persists values across Streamlit reruns (user interactions).
# Think of it as a dict that survives widget callbacks within one browser session.
if "history" not in st.session_state:
    st.session_state.history = []  # list of {question, answer, parents, children}

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Configuration")
    api_key = st.text_input(
        "Mistral API key",
        type="password",
        value=os.environ.get("MISTRAL_API_KEY", ""),
        help="Get a free key at console.mistral.ai",
    )
    strategy = st.selectbox(
        "Retrieval strategy",
        ["hybrid+rerank", "hybrid", "dense"],
        index=0,
    )
    top_k = st.slider("Candidate pool (top_k)", 10, 80, 40, step=10)
    top_n = st.slider("Reranked results (top_n)", 3, 10, 5)
    st.divider()
    if st.button("🗑️ Clear history"):
        st.session_state.history = []
        st.rerun()

# ── Lazy init (cached across reruns) ─────────────────────────────────────────


# @st.cache_resource means the object is created once and reused — ideal for
# heavyweight clients like Qdrant connections and Mistral API wrappers.
@st.cache_resource
def get_qdrant():
    return get_client()


@st.cache_resource
def get_generator(key: str):
    return ComplianceGenerator(api_key=key)


PARENTS_PATH = ROOT / "data" / "processed" / "chunks_parents.jsonl"

# ── Query UI ──────────────────────────────────────────────────────────────────

col_input, col_btn = st.columns([5, 1])
with col_input:
    question = st.text_input(
        "Question",
        placeholder="Welche Rechte haben betroffene Personen unter der DSGVO?",
        label_visibility="collapsed",
    )
with col_btn:
    run = st.button("Ask ▶", use_container_width=True)

# ── Main generation flow ──────────────────────────────────────────────────────

if run and question.strip():
    if not api_key:
        st.error("Add your Mistral API key in the sidebar.")
        st.stop()

    try:
        qdrant = get_qdrant()
        generator = get_generator(api_key)

        with st.spinner("Retrieving relevant articles…"):
            children, parents = retrieve_with_parents(
                client=qdrant,
                query=question,
                parents_path=PARENTS_PATH,
                strategy=strategy,
                top_k=top_k,
                top_n=top_n,
            )

        st.markdown(
            f'<div class="metric-row">'
            f'<div class="metric">strategy <span>{strategy}</span></div>'
            f'<div class="metric">children retrieved <span>{len(children)}</span></div>'
            f'<div class="metric">parent contexts <span>{len(parents)}</span></div>'
            f"</div>",
            unsafe_allow_html=True,
        )

        st.markdown("**Answer**")
        answer_placeholder = st.empty()
        full_answer = ""
        with answer_placeholder.container():
            full_answer = st.write_stream(generator.stream(question, parents))

        with st.expander(f"📄 Sources ({len(parents)} parent chunks)", expanded=False):
            for i, p in enumerate(parents, 1):
                meta = p.get("metadata", {})
                heading = meta.get("section_heading", "Unknown")
                source = meta.get("source_doc", "?")
                page = meta.get("page_start", "?")
                text_preview = p.get("text", "")[:400].replace("\n", " ")
                st.markdown(
                    f'<div class="source-card">'
                    f"<code>[{i}] {heading} · p.{page} · {source}</code><br>"
                    f"{text_preview}…"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        st.session_state.history.append(
            {
                "question": question,
                "answer": full_answer,
                "parents": parents,
                "children": children,
            }
        )

    except Exception as e:
        st.exception(e)  # renders full traceback in the UI

# ── Conversation history ──────────────────────────────────────────────────────

if st.session_state.history:
    st.divider()
    st.markdown("### History")
    # Reverse so newest is first
    for turn in reversed(st.session_state.history[:-1]):  # skip last (just shown above)
        with st.expander(f"Q: {turn['question'][:80]}…"):
            st.markdown(
                f'<div class="answer-box">{turn["answer"]}</div>',
                unsafe_allow_html=True,
            )
