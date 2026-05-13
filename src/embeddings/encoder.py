"""Dense and sparse embedding generation for the Munich RAG corpus.

Dense:  multilingual-e5-large-instruct (1024-dim)
Sparse: BM25 via fastembed

CRITICAL: e5 models require prefixes — 'passage: ' for documents,
'query: ' for queries. These are applied at the API boundary so callers
cannot accidentally embed without them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from fastembed import SparseEmbedding, SparseTextEmbedding
from sentence_transformers import SentenceTransformer

from src.logging_setup import get_logger

log = get_logger(__name__)

DENSE_MODEL_NAME = "intfloat/multilingual-e5-large-instruct"
DENSE_DIM = 1024

# OLD:
# SPARSE_MODEL_NAME = "Qdrant/bm25"
# NEW:
SPARSE_MODEL_NAME = "Qdrant/bm42-all-minilm-l6-v2-attentions"

# e5 prefixes — wrong values silently degrade retrieval quality
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


@lru_cache(maxsize=1)
def get_dense_model() -> SentenceTransformer:
    """Lazy-load the dense embedding model. Cached for process lifetime."""
    log.info("loading_dense_model", model=DENSE_MODEL_NAME)
    model = SentenceTransformer(DENSE_MODEL_NAME, device="cpu")
    log.info("dense_model_loaded", dim=DENSE_DIM)
    return model


@lru_cache(maxsize=1)
def get_sparse_model() -> SparseTextEmbedding:
    """Lazy-load the BM25 sparse model."""
    log.info("loading_sparse_model", model=SPARSE_MODEL_NAME)
    return SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)


def encode_passages_dense(
    texts: list[str],
    batch_size: int = 16,
    show_progress: bool = True,
) -> np.ndarray:
    """Embed a list of passages (documents/chunks).

    The texts must NOT already have the 'passage: ' prefix — this function adds it.
    Returns: array of shape (len(texts), 1024), L2-normalized.
    """
    if not texts:
        return np.zeros((0, DENSE_DIM), dtype=np.float32)

    model = get_dense_model()
    prefixed = [PASSAGE_PREFIX + t for t in texts]
    embeddings = model.encode(
        prefixed,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        normalize_embeddings=True,  # cosine similarity = dot product on unit vectors
        convert_to_numpy=True,
    )
    return embeddings.astype(np.float32)


def encode_query_dense(text: str) -> np.ndarray:
    """Embed a single query string. Applies 'query: ' prefix.

    Returns: array of shape (1024,), L2-normalized.
    """
    model = get_dense_model()
    prefixed = QUERY_PREFIX + text
    embedding = model.encode(
        [prefixed],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )[0]
    return embedding.astype(np.float32)


def encode_passages_sparse(texts: list[str]) -> list[SparseEmbedding]:
    """Generate BM25 sparse embeddings for a list of passages.

    Returns: list of SparseEmbedding objects (indices + values).
    """
    if not texts:
        return []
    model = get_sparse_model()
    return list(model.embed(texts))


def encode_query_sparse(text: str) -> SparseEmbedding:
    """Generate BM25 sparse embedding for a single query."""
    model = get_sparse_model()
    return next(iter(model.query_embed([text])))
