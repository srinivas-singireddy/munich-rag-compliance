"""Hybrid search combining dense (semantic) and sparse (BM25) retrieval via RRF.

Two-stage pipeline:
    Stage 1: Qdrant hybrid query → top-K candidates (dense + BM25 combined)
    Stage 2: Cross-encoder reranker → top-N final results

This module is the primary retrieval interface for the RAG system.
All downstream code (generation, eval, UI) should use retrieve() or
retrieve_with_parents().
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sentence_transformers import CrossEncoder

from src.embeddings.encoder import encode_query_dense, encode_query_sparse
from src.logging_setup import get_logger
from src.retrieval.vector_store import COLLECTION_NAME, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME

log = get_logger(__name__)

# Cross-encoder model — multilingual, German-strong, open-weight
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# Retrieval knobs
HYBRID_CANDIDATE_K = 40  # how many candidates to retrieve before reranking
RERANKER_TOP_N = 5  # how many to return after reranking
RRF_K = 100  # RRF constant — robust default, rarely needs tuning


@dataclass
class RetrievalResult:
    """Single retrieved result with all metadata needed for generation + citation."""

    chunk_id: str
    parent_id: str
    score: float  # final score (reranker score if reranked, else RRF)
    text_raw: str  # chunk body (no prefix) — shown in citations
    text_for_embedding: str  # chunk with prefix — what was indexed
    token_count: int
    metadata: dict
    reranker_score: float | None = None
    retrieval_strategy: str = "hybrid"


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    """Lazy-load the cross-encoder reranker. Cached for process lifetime."""
    log.info("loading_reranker", model=RERANKER_MODEL)
    model = CrossEncoder(RERANKER_MODEL, max_length=512)
    log.info("reranker_loaded")
    return model


def _qdrant_to_result(point: qm.ScoredPoint, strategy: str) -> RetrievalResult:
    """Convert a Qdrant ScoredPoint to a RetrievalResult."""
    payload = point.payload or {}
    return RetrievalResult(
        chunk_id=payload.get("chunk_id", ""),
        parent_id=payload.get("parent_id", ""),
        score=point.score,
        text_raw=payload.get("text_raw", ""),
        text_for_embedding=payload.get("text_for_embedding", ""),
        token_count=payload.get("token_count", 0),
        metadata=payload.get("metadata", {}),
        retrieval_strategy=strategy,
    )


def search_dense(
    client: QdrantClient,
    query: str,
    top_k: int = HYBRID_CANDIDATE_K,
    filter_: qm.Filter | None = None,
) -> list[RetrievalResult]:
    """Dense-only search. Baseline for comparison."""
    qvec = encode_query_dense(query)
    points = client.query_points(
        collection_name=COLLECTION_NAME,
        query=qvec.tolist(),
        using=DENSE_VECTOR_NAME,
        limit=top_k,
        query_filter=filter_,
        with_payload=True,
    ).points
    return [_qdrant_to_result(p, "dense") for p in points]


def search_hybrid(
    client: QdrantClient,
    query: str,
    top_k: int = HYBRID_CANDIDATE_K,
    filter_: qm.Filter | None = None,
) -> list[RetrievalResult]:
    """Hybrid search: dense + BM25 combined via Reciprocal Rank Fusion."""
    dense_vec = encode_query_dense(query)
    sparse_vec = encode_query_sparse(query)

    points = client.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            qm.Prefetch(
                query=dense_vec.tolist(),
                using=DENSE_VECTOR_NAME,
                limit=top_k,
            ),
            qm.Prefetch(
                query=qm.SparseVector(
                    indices=sparse_vec.indices.tolist(),
                    values=sparse_vec.values.tolist(),
                ),
                using=SPARSE_VECTOR_NAME,
                limit=top_k,
            ),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        limit=top_k,
        query_filter=filter_,
        with_payload=True,
    ).points

    return [_qdrant_to_result(p, "hybrid") for p in points]


def rerank(
    query: str,
    candidates: list[RetrievalResult],
    top_n: int = RERANKER_TOP_N,
) -> list[RetrievalResult]:
    """Cross-encoder reranking of hybrid candidates.

    Takes top-K hybrid results and returns top-N reranked results.
    The cross-encoder sees (query, chunk_text) pairs and scores them jointly
    — much more accurate than the bi-encoder similarity used at retrieval time.
    """
    if not candidates:
        return []

    reranker = get_reranker()
    # Use text_for_embedding (includes context prefix) not text_raw
    # The prefix contains document title + section heading which helps the
    # reranker understand what article it's reading
    pairs = [[query, c.text_for_embedding] for c in candidates]
    scores: list[float] = reranker.predict(pairs).tolist()

    for candidate, score in zip(candidates, scores):
        candidate.reranker_score = score
        candidate.retrieval_strategy = "hybrid+rerank"

    reranked = sorted(candidates, key=lambda c: c.reranker_score or 0, reverse=True)
    return reranked[:top_n]


def retrieve(
    client: QdrantClient,
    query: str,
    strategy: str = "hybrid+rerank",
    top_k: int = HYBRID_CANDIDATE_K,
    top_n: int = RERANKER_TOP_N,
    filter_: qm.Filter | None = None,
) -> list[RetrievalResult]:
    """Main retrieval interface. Strategy controls the pipeline.

    Strategies:
        'dense'         — dense-only, no reranking (baseline)
        'hybrid'        — RRF fusion, no reranking
        'hybrid+rerank' — RRF fusion + cross-encoder reranking (default)
    """
    if strategy == "dense":
        return search_dense(client, query, top_k=top_n, filter_=filter_)
    elif strategy == "hybrid":
        return search_hybrid(client, query, top_k=top_k, filter_=filter_)[:top_n]
    elif strategy == "hybrid+rerank":
        candidates = search_hybrid(client, query, top_k=top_k, filter_=filter_)
        return rerank(query, candidates, top_n=top_n)
    else:
        raise ValueError(
            f"Unknown strategy: {strategy!r}. Use 'dense', 'hybrid', or 'hybrid+rerank'."
        )


def retrieve_with_parents(
    client: QdrantClient,
    query: str,
    parents_path: Path,
    strategy: str = "hybrid+rerank",
    top_k: int = HYBRID_CANDIDATE_K,
    top_n: int = RERANKER_TOP_N,
) -> tuple[list[RetrievalResult], list[dict]]:
    """Retrieve children, then load their parent chunks for LLM context.

    Returns: (child_results, parent_dicts)
    The parent_dicts are the large chunks to send to the LLM.
    The child_results carry the metadata for citations.
    """
    children = retrieve(client, query, strategy=strategy, top_k=top_k, top_n=top_n)

    if not children:
        return [], []

    # Load parents file once
    import json

    parents_by_id: dict[str, dict] = {}
    for line in parents_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            p = json.loads(line)
            parents_by_id[p["chunk_id"]] = p

    # Collect unique parents in retrieval order
    seen_parents: set[str] = set()
    parents: list[dict] = []
    for child in children:
        pid = child.parent_id
        if pid not in seen_parents and pid in parents_by_id:
            seen_parents.add(pid)
            parents.append(parents_by_id[pid])

    return children, parents
