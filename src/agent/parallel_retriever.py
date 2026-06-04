# src/agent/parallel_retriever.py
"""
Parallel retrieval for multi-article compliance queries.

Strategy:
  - One retrieve() call per article reference, each with an article-scoped query
  - Executed concurrently via ThreadPoolExecutor (retrieve() is sync/blocking)
  - Results merged: deduplicate by chunk_id, keep highest score per chunk
  - Output sorted by score descending — same shape as single retrieve() output
"""

import concurrent.futures
from typing import Any

import structlog
from qdrant_client import QdrantClient

from src.retrieval.hybrid_search import retrieve, get_reranker
from src.embeddings.encoder import get_dense_model, get_sparse_model

log = structlog.get_logger()

# Warm up both models once at import time — before any threads start.
# get_dense_model() and get_sparse_model() use @lru_cache(maxsize=1),
# so this single call populates the cache. All threads then hit the cache,
# never the loader — eliminating the Half/Float dtype race condition.
get_dense_model()
get_sparse_model()
get_reranker()

# Cap workers — we have at most ~5 article refs in realistic queries
_MAX_WORKERS = 5


def _retrieve_for_article(
    client: QdrantClient,
    base_query: str,
    article_ref: str,  # e.g. "art_28"
    top_k: int,
) -> list[dict]:
    """
    Single retrieve() call scoped to one article.
    Builds a focused sub-query: "Art. 28 <original query context>".
    Returns list of serialised chunk dicts (same shape as retriever node output).
    """
    # Build article-scoped sub-query — prepend article number for embedding focus
    article_num = article_ref.replace("art_", "")
    sub_query = f"Artikel {article_num} DSGVO"

    log.info("parallel_retrieve.start", article=article_ref, sub_query=sub_query)

    results = retrieve(
        client=client,
        query=sub_query,
        strategy="hybrid+rerank",
        top_k=top_k,
    )

    serialised = [
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

    log.info("parallel_retrieve.done", article=article_ref, n_chunks=len(serialised))
    return serialised


def parallel_retrieve(
    client: QdrantClient,
    query: str,
    article_refs: list[str],
    top_k: int = 10,
) -> list[dict]:
    """
    Dispatch one retrieve() call per article_ref concurrently.
    Merge results: dedup by chunk_id, keep best score, sort descending.

    Returns list of chunk dicts — same shape as single retrieve() output,
    ready to be passed directly to context_assembler.
    """
    if not article_refs:
        log.warning("parallel_retrieve.no_refs", query=query)
        return []

    all_chunks: list[dict] = []

    # ThreadPoolExecutor — correct choice here because retrieve() is I/O-bound
    # (network calls to Qdrant + HTTP to Mistral reranker).
    # asyncio would require async-native versions of those clients.
    # New Python pattern: submit() returns Future objects; as_completed()
    # yields them as each finishes (not in submission order).
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_retrieve_for_article, client, query, ref, top_k): ref
            for ref in article_refs
        }

        for future in concurrent.futures.as_completed(futures):
            ref = futures[future]
            try:
                chunks = future.result()
                all_chunks.extend(chunks)
            except Exception as exc:
                log.error("parallel_retrieve.error", article=ref, error=str(exc))

    # Merge: dedup by chunk_id, keep highest score
    # New Python pattern: dict comprehension used as a fold —
    # we iterate and conditionally update, building best-score map in one pass.
    best: dict[str, dict] = {}
    for chunk in all_chunks:
        cid = chunk["id"]
        if cid not in best or chunk["score"] > best[cid]["score"]:
            best[cid] = chunk

    merged = sorted(best.values(), key=lambda c: c["score"], reverse=True)
    log.info("parallel_retrieve.merged", total_before=len(all_chunks), after_dedup=len(merged))
    return merged
