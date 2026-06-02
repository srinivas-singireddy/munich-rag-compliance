"""
Agent data contracts.

Two models live here:
- AgentState: TypedDict that LangGraph passes between nodes (mutable, incremental)
- ComplianceResponse: Pydantic model returned to the caller (complete, typed)

Why TypedDict for AgentState and not Pydantic:
LangGraph does partial state updates — each node returns only the keys it changed.
Pydantic requires all fields at construction. TypedDict allows incremental population.
"""

from __future__ import annotations

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Query classification enum
# ---------------------------------------------------------------------------


class QueryType(str, Enum):
    SIMPLE_RAG = "simple_rag"
    MULTI_ARTICLE = "multi_article"
    OUT_OF_SCOPE = "out_of_scope"


# ---------------------------------------------------------------------------
# LangGraph shared state — TypedDict, not Pydantic
# Each node receives this full dict and returns a partial dict of changed keys
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    """
    Shared state passed between all agent nodes.

    total=False means all keys are optional at construction.
    Each node populates only the keys it is responsible for.

    Key ownership:
        query           → set by caller at graph entry, never mutated
        query_type      → set by query_classifier node
        retrieved_chunks→ set by retriever node (list of Qdrant ScoredPoint dicts)
        parent_chunks   → set by context_assembler node (deduplicated parent dicts)
        answer          → set by generator node
        raw_citations   → set by generator node (citations as returned by Mistral)
        validated_citations → set by citation_validator node
        confidence      → set by citation_validator node
        error           → set by any node on failure
    """

    query: str
    query_type: str
    retrieved_chunks: list[dict]
    parent_chunks: list[dict]
    answer: str
    raw_citations: list[str]
    validated_citations: list[str]
    confidence: float
    error: str | None


# ---------------------------------------------------------------------------
# Final output — Pydantic model, constructed once by citation_validator
# ---------------------------------------------------------------------------


class ComplianceResponse(BaseModel):
    """
    Typed output returned to all callers (Streamlit, API, eval harness).

    Fields:
        answer              Natural language answer from Mistral
        citations           Article IDs verified to exist in retrieved context
        confidence          0.0–1.0, derived from reranker scores of top chunks
        query_type          Classification assigned by query_classifier node
        retrieved_sections  section_ids of all retrieved chunks (audit trail)
        out_of_scope        True if query was rejected at classifier — no retrieval
        error               Non-None if any node failed gracefully
    """

    answer: str = Field(description="Natural language compliance answer")
    citations: list[str] = Field(
        default_factory=list, description="Article IDs verified present in retrieved context"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score derived from reranker scores"
    )
    query_type: QueryType = Field(description="Query classification")
    retrieved_sections: list[str] = Field(
        default_factory=list, description="section_ids of all retrieved chunks for audit trail"
    )
    out_of_scope: bool = Field(
        default=False, description="True if query was rejected at classifier"
    )
    error: str | None = Field(
        default=None, description="Error message if any node failed gracefully"
    )
