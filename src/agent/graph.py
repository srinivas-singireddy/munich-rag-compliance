"""
LangGraph StateGraph wiring for munich-rag-compliance agent.

Graph topology:
    START
      ↓
    query_classifier
      ↓
      ├── out_of_scope  → END  (short-circuit, no retrieval or generation)
      ├── simple_rag    → retriever → context_assembler → generator → citation_validator → END
      └── multi_article → retriever → context_assembler → generator → citation_validator → END

Multi-article parallel retrieval is scaffolded here and completed in Days 9-10.
For Day 8, multi_article follows the same path as simple_rag — sequential retrieval.
"""

from __future__ import annotations

import structlog
from langgraph.graph import StateGraph, END

from src.agent.models import AgentState, ComplianceResponse, QueryType
from src.agent.nodes import (
    query_classifier,
    retriever,
    context_assembler,
    generator,
    citation_validator,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Routing function — reads state after query_classifier, decides next node
# ---------------------------------------------------------------------------


def _route_after_classifier(state: AgentState) -> str:
    """
    Conditional routing function called after query_classifier node.

    Returns a string key that LangGraph maps to the next node.
    Keys must match the dict passed to add_conditional_edges().

    out_of_scope → "end"          (short-circuit to END)
    simple_rag   → "retrieve"     (standard RAG path)
    multi_article→ "retrieve"     (same path for Day 8 — parallel in Days 9-10)
    """
    query_type = state.get("query_type", QueryType.SIMPLE_RAG.value)
    logger.info("router.decision", query_type=query_type)

    if query_type == QueryType.OUT_OF_SCOPE.value:
        return "end"
    return "retrieve"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """
    Construct and compile the compliance agent StateGraph.

    Returns a compiled graph ready to invoke.
    Call once at application startup — compilation is not free.
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("query_classifier", query_classifier)
    graph.add_node("retriever", retriever)
    graph.add_node("context_assembler", context_assembler)
    graph.add_node("generator", generator)
    graph.add_node("citation_validator", citation_validator)

    # Entry point
    graph.set_entry_point("query_classifier")

    # Conditional routing after classifier
    graph.add_conditional_edges(
        "query_classifier",
        _route_after_classifier,
        {
            "end": END,  # out_of_scope — stop here
            "retrieve": "retriever",  # all other types — continue
        },
    )

    # Linear path for simple_rag and multi_article (Day 8)
    graph.add_edge("retriever", "context_assembler")
    graph.add_edge("context_assembler", "generator")
    graph.add_edge("generator", "citation_validator")
    graph.add_edge("citation_validator", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Response builder — converts final AgentState to ComplianceResponse
# ---------------------------------------------------------------------------


def build_response(state: AgentState) -> ComplianceResponse:
    """
    Convert final AgentState into a typed ComplianceResponse.

    Called after graph.invoke() returns. The state at that point contains
    all keys populated by every node that ran.

    For out_of_scope queries, most keys will be absent — we use .get()
    with safe defaults throughout.
    """
    query_type_str = state.get("query_type", QueryType.SIMPLE_RAG.value)

    # Safely coerce string to QueryType enum
    # dict lookup is cleaner than try/except for known finite value sets
    query_type = QueryType(query_type_str)

    is_out_of_scope = query_type == QueryType.OUT_OF_SCOPE

    # Build retrieved_sections audit trail from child chunk payloads
    retrieved_sections = list(
        {
            chunk["payload"].get("section_id", "")
            for chunk in state.get("retrieved_chunks", [])
            if chunk.get("payload") and chunk["payload"].get("section_id")
        }
    )

    return ComplianceResponse(
        answer=state.get("answer") or _out_of_scope_message(state),
        citations=state.get("validated_citations", []),
        confidence=state.get("confidence", 0.0),
        query_type=query_type,
        retrieved_sections=retrieved_sections,
        out_of_scope=is_out_of_scope,
        error=state.get("error"),
    )


def _out_of_scope_message(state: AgentState) -> str:
    """
    Polite rejection message for out_of_scope queries.
    Only called when answer is absent — i.e. graph short-circuited at classifier.
    """
    return (
        "This system answers questions about DSGVO and BDSG only. "
        "Your query does not appear to be related to German data protection law. "
        "Please rephrase or ask a compliance-related question."
    )


# ---------------------------------------------------------------------------
# Public API — single entry point for all callers
# ---------------------------------------------------------------------------

# Compiled graph — built once at import time
_compiled_graph = build_graph()


def run_agent(query: str) -> ComplianceResponse:
    """
    Main entry point for the compliance agent.

    Usage:
        from src.agent.graph import run_agent
        response = run_agent("What does Art. 5 DSGVO say?")
        print(response.answer)
        print(response.citations)

    Args:
        query: Natural language compliance question

    Returns:
        ComplianceResponse with answer, verified citations, confidence,
        query type, and audit trail of retrieved sections.
    """
    logger.info("agent.start", query=query)

    final_state: AgentState = _compiled_graph.invoke({"query": query})

    response = build_response(final_state)

    logger.info(
        "agent.done",
        query_type=response.query_type,
        citation_count=len(response.citations),
        confidence=response.confidence,
        out_of_scope=response.out_of_scope,
    )

    return response
