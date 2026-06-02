"""
LangGraph agent nodes for munich-rag-compliance.

Each node is a plain function: (AgentState) -> dict
The returned dict is a partial state update — only changed keys.

Node responsibilities:
    query_classifier    → classify query type (LLM call)
    retriever           → retrieve chunks via hybrid search (no LLM)
    context_assembler   → deduplicate + rank parent chunks (no LLM)
    generator           → generate answer via Mistral (LLM call)
    citation_validator  → verify citations exist in context (no LLM)
"""

from __future__ import annotations

import json
import os
import re
import structlog

from mistralai import Mistral

from src.agent.models import AgentState, QueryType
from src.retrieval.hybrid_search import retrieve, RetrievalResult
from src.retrieval.vector_store import get_client
from src.generation.generator import ComplianceGenerator

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared clients — initialised once at module load
# ---------------------------------------------------------------------------

_mistral_client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
_qdrant_client = get_client()
_generator = ComplianceGenerator()

# ---------------------------------------------------------------------------
# Node 1 — query_classifier
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM_PROMPT = """\
You are a query classifier for a German data protection compliance system.
The system covers exactly two corpora: DSGVO (German GDPR) and BDSG (German Federal Data Protection Act).

Classify the user query into exactly one of these three classes:

  simple_rag    — single article lookup, definition, or explanation
                  Examples: "What does Art. 5 DSGVO say?", "Define personal data under GDPR"

  multi_article — comparison, cross-reference, or aggregation across multiple articles or corpora
                  Examples: "Compare Art. 28 and Art. 29 DSGVO", "What do both DSGVO and BDSG say about employee data?"

  out_of_scope  — unrelated to DSGVO, BDSG, or data protection law
                  Examples: "What is the weather in Munich?", "Explain quantum computing"

Respond with a JSON object only. No preamble. No explanation. No markdown.
Format: {"classification": "<class>", "reasoning": "<one sentence>"}
"""


def query_classifier(state: AgentState) -> dict:
    """
    Classify incoming query as simple_rag, multi_article, or out_of_scope.

    Makes a constrained Mistral call. On any failure (API error, parse error,
    unexpected class value), defaults to simple_rag — never crashes the graph.

    Returns partial state update: {"query_type": str}
    """
    query = state["query"]
    logger.info("query_classifier.start", query=query)

    try:
        response = _mistral_client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            max_tokens=100,
            temperature=0.0,
        )

        raw = response.choices[0].message.content.strip()
        logger.debug("query_classifier.raw_response", raw=raw)

        # Strip markdown fences if Mistral wraps in ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()

        parsed = json.loads(raw)
        classification = parsed.get("classification", "simple_rag").strip()
        reasoning = parsed.get("reasoning", "")

        valid_classes = {q.value for q in QueryType}
        if classification not in valid_classes:
            logger.warning(
                "query_classifier.unexpected_class",
                classification=classification,
                defaulting_to="simple_rag",
            )
            classification = QueryType.SIMPLE_RAG.value

        logger.info(
            "query_classifier.done",
            query_type=classification,
            reasoning=reasoning,
        )
        return {"query_type": classification}

    except Exception as e:
        logger.error("query_classifier.failed", error=str(e), defaulting_to="simple_rag")
        return {"query_type": QueryType.SIMPLE_RAG.value}


# ---------------------------------------------------------------------------
# Node 2 — retriever
# ---------------------------------------------------------------------------


def retriever(state: AgentState) -> dict:
    """
    Retrieve relevant chunks using existing hybrid+rerank pipeline.

    retrieve() requires a QdrantClient — we pass the module-level shared client.
    RetrievalResult is a dataclass — we serialise to plain dicts for state storage.

    Returns partial state update: {"retrieved_chunks": list[dict]}
    """
    query = state["query"]
    query_type = state.get("query_type", QueryType.SIMPLE_RAG.value)

    logger.info("retriever.start", query=query, query_type=query_type)

    if query_type == QueryType.OUT_OF_SCOPE.value:
        logger.info("retriever.skipped", reason="out_of_scope")
        return {"retrieved_chunks": []}

    try:
        results: list[RetrievalResult] = retrieve(
            client=_qdrant_client,
            query=query,
            strategy="hybrid+rerank",
        )

        # Serialise RetrievalResult dataclass to plain dict
        # We keep only the fields downstream nodes need
        chunks = [
            {
                "id": r.chunk_id,
                "score": r.score,
                "payload": {
                    "section_id": r.metadata.get("section_id"),
                    "parent_id": r.parent_id,
                    "doc_id": r.metadata.get("doc_id"),
                    "text_raw": r.text_raw,
                },
            }
            for r in results
        ]

        logger.info("retriever.done", chunk_count=len(chunks))
        return {"retrieved_chunks": chunks}

    except Exception as e:
        logger.error("retriever.failed", error=str(e))
        return {"retrieved_chunks": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Node 3 — context_assembler
# ---------------------------------------------------------------------------


def context_assembler(state: AgentState) -> dict:
    """
    Assemble parent chunks from retrieved child chunks.

    Steps:
      1. Extract parent_id from each retrieved child chunk payload
      2. Load corresponding parent chunk from chunks_parents.jsonl
      3. Deduplicate by parent_id (multiple children can share a parent)
      4. Sort by max child score per parent — highest relevance first

    Critical: parent metadata is nested under "metadata" key — not top-level.
    Always p["metadata"]["doc_id"], never p.get("doc_id"). See project L-003.

    Returns partial state update: {"parent_chunks": list[dict]}
    """
    retrieved_chunks = state.get("retrieved_chunks", [])

    if not retrieved_chunks:
        logger.info("context_assembler.skipped", reason="no_retrieved_chunks")
        return {"parent_chunks": []}

    logger.info("context_assembler.start", child_count=len(retrieved_chunks))

    # Build parent_id → max child score mapping
    parent_scores: dict[str, float] = {}
    for chunk in retrieved_chunks:
        parent_id = chunk["payload"].get("parent_id")
        score = chunk.get("score", 0.0)
        if parent_id:
            if parent_id not in parent_scores or score > parent_scores[parent_id]:
                parent_scores[parent_id] = score

    if not parent_scores:
        logger.warning("context_assembler.no_parent_ids_found")
        return {"parent_chunks": []}

    # Load parent chunks from disk and match against retrieved parent_ids
    from src.config import settings

    parents_path = settings.processed_dir / "chunks_parents.jsonl"
    parent_map: dict[str, dict] = {}

    with open(parents_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            # metadata is nested — always access via p["metadata"]
            pid = p.get("chunk_id")
            if pid and pid in parent_scores:
                parent_map[pid] = p

    # Sort by best child score descending
    sorted_parents = sorted(
        parent_map.values(),
        key=lambda p: parent_scores.get(
            p.get("chunk_id"),
            0.0,
        ),
        reverse=True,
    )

    logger.info(
        "context_assembler.done",
        parent_count=len(sorted_parents),
        requested_parent_ids=len(parent_scores),
    )
    return {"parent_chunks": sorted_parents}


# ---------------------------------------------------------------------------
# Node 4 — generator
# ---------------------------------------------------------------------------


def generator(state: AgentState) -> dict:
    """
    Generate compliance answer from assembled parent chunks.

    ComplianceGenerator.generate() signature:
        generate(question: str, parents: list[dict]) -> str

    Returns a plain answer string — not a dict.
    Citations are extracted from the answer text by citation_validator.

    Returns partial state update: {"answer": str, "raw_citations": list[str]}
    """
    query = state["query"]
    parent_chunks = state.get("parent_chunks", [])

    if not parent_chunks:
        logger.warning("generator.no_context", query=query)
        return {
            "answer": "I could not find relevant information in the compliance corpus.",
            "raw_citations": [],
        }

    logger.info("generator.start", query=query, parent_count=len(parent_chunks))

    try:
        answer: str = _generator.generate(question=query, parents=parent_chunks)

        # Extract article references from answer text
        # Matches patterns like: Art. 5, Art.5, art_5, Article 28
        # re.findall returns all non-overlapping matches as a list of strings
        raw_citations = list(
            set(
                re.findall(
                    r"\bart(?:ikel|icle)?[\s\._]*(\d+)\b",
                    answer,
                    flags=re.IGNORECASE,
                )
            )
        )
        # Normalise to art_N format for citation_validator
        raw_citations = [f"art_{n}" for n in raw_citations]

        logger.info(
            "generator.done", answer_chars=len(answer), raw_citation_count=len(raw_citations)
        )
        return {"answer": answer, "raw_citations": raw_citations}

    except Exception as e:
        logger.error("generator.failed", error=str(e))
        return {
            "answer": "An error occurred during answer generation.",
            "raw_citations": [],
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Node 5 — citation_validator
# ---------------------------------------------------------------------------


def citation_validator(state: AgentState) -> dict:
    """
    Verify that cited article IDs exist in the retrieved context.

    Normalises citation strings extracted from answer text (e.g. "Art. 5",
    "art_5", "art.5") to section_id format (e.g. "art_5") before checking
    against retrieved chunk payloads.

    Confidence is mean reranker score of top-3 retrieved chunks.

    Returns partial state update:
        {"validated_citations": list[str], "confidence": float}
    """
    raw_citations = state.get("raw_citations", [])
    retrieved_chunks = state.get("retrieved_chunks", [])

    logger.info(
        "citation_validator.start",
        raw_citation_count=len(raw_citations),
        retrieved_chunk_count=len(retrieved_chunks),
    )

    # Build set of section_ids present in retrieved context
    retrieved_section_ids: set[str] = {
        chunk["payload"].get("section_id", "") for chunk in retrieved_chunks if chunk.get("payload")
    }

    def _normalise(citation: str) -> str:
        """
        Normalise citation string to section_id format.
        "Art. 5" → "art_5", "art.28" → "art_28", "Article 5" → "art_5"
        """
        # Extract the numeric part, reconstruct as art_N
        digits = re.search(r"\d+", citation)
        if digits:
            return f"art_{digits.group()}"
        return citation.lower()

    normalised = [_normalise(c) for c in raw_citations]
    validated = [c for c in normalised if c in retrieved_section_ids]
    hallucinated = [c for c in normalised if c not in retrieved_section_ids]

    if hallucinated:
        logger.warning(
            "citation_validator.hallucinated_citations",
            hallucinated=hallucinated,
            validated=validated,
        )

    # Confidence: mean score of top-3 retrieved chunks
    top_scores = sorted(
        [c.get("score", 0.0) for c in retrieved_chunks],
        reverse=True,
    )[:3]
    confidence = sum(top_scores) / len(top_scores) if top_scores else 0.0

    logger.info(
        "citation_validator.done",
        validated_count=len(validated),
        hallucinated_count=len(hallucinated),
        confidence=round(confidence, 3),
    )

    return {
        "validated_citations": validated,
        "confidence": round(confidence, 3),
    }
