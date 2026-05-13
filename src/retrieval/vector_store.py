"""Qdrant collection management and bulk upsert.

Collection design:
    - Single collection 'munich_rag_children'
    - Named dense vector 'dense' (1024-dim, cosine distance)
    - Named sparse vector 'sparse' (BM25)
    - Payload carries full ChunkMetadata + parent_id + text
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from rich.progress import Progress

from src.embeddings.encoder import (
    DENSE_DIM,
    encode_passages_dense,
    encode_passages_sparse,
)
from src.logging_setup import get_logger

log = get_logger(__name__)

COLLECTION_NAME = "munich_rag_children"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


def get_client(host: str = "localhost", port: int = 6333) -> QdrantClient:
    """Create a Qdrant client connected to local docker-compose instance."""
    return QdrantClient(host=host, port=port, timeout=30.0)


def ensure_collection(client: QdrantClient, recreate: bool = False) -> None:
    """Create the collection if it doesn't exist (or recreate if requested)."""
    existing = [c.name for c in client.get_collections().collections]

    if COLLECTION_NAME in existing:
        if recreate:
            log.info("recreating_collection", name=COLLECTION_NAME)
            client.delete_collection(COLLECTION_NAME)
        else:
            log.info("collection_exists", name=COLLECTION_NAME)
            return

    log.info("creating_collection", name=COLLECTION_NAME, dim=DENSE_DIM)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            DENSE_VECTOR_NAME: qm.VectorParams(
                size=DENSE_DIM,
                distance=qm.Distance.COSINE,
                on_disk=False,  # in-memory for our corpus size
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: qm.SparseVectorParams(
                index=qm.SparseIndexParams(on_disk=False),
                # No IDF modifier — bm42 computes its own attention-weighted scores
            ),
        },
        hnsw_config=qm.HnswConfigDiff(
            m=16,  # standard
            ef_construct=128,
        ),
    )

    # Create payload indexes for filterable fields — enables fast metadata filtering
    for field in ("metadata.doc_id", "metadata.language", "metadata.section_type"):
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )

    # NEW: section_numbers is a list — Qdrant indexes each element so we can
    # filter "give me all chunks where section_numbers contains '14'"
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="metadata.section_numbers",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )

    log.info("collection_ready", name=COLLECTION_NAME)


def upsert_children(
    client: QdrantClient,
    children: list[dict],
    batch_size: int = 32,
) -> None:
    """Embed and upsert children into Qdrant.

    Children are dicts loaded from chunks_children.jsonl.
    Idempotent: re-running with the same chunk_ids overwrites payloads.
    """
    if not children:
        log.warning("no_children_to_upsert")
        return

    log.info("upsert_start", total=len(children), batch_size=batch_size)

    with Progress() as progress:
        embed_task = progress.add_task("Embedding (dense)", total=len(children))
        sparse_task = progress.add_task("Embedding (sparse)", total=len(children))
        upsert_task = progress.add_task("Upserting", total=len(children))

        for batch_start in range(0, len(children), batch_size):
            batch = children[batch_start : batch_start + batch_size]
            texts = [c["text_for_embedding"] for c in batch]

            # Dense embeddings (vectorized batch)
            dense_vecs = encode_passages_dense(texts, batch_size=batch_size, show_progress=False)
            progress.advance(embed_task, len(batch))

            # Sparse embeddings
            sparse_vecs = encode_passages_sparse(texts)
            progress.advance(sparse_task, len(batch))

            # Build points
            points = []
            for child, dvec, svec in zip(batch, dense_vecs, sparse_vecs):
                points.append(
                    qm.PointStruct(
                        id=_chunk_id_to_uuid(child["chunk_id"]),
                        vector={
                            DENSE_VECTOR_NAME: dvec.tolist(),
                            SPARSE_VECTOR_NAME: qm.SparseVector(
                                indices=svec.indices.tolist(),
                                values=svec.values.tolist(),
                            ),
                        },
                        payload={
                            "chunk_id": child["chunk_id"],
                            "parent_id": child["parent_id"],
                            "text_raw": child["text_raw"],
                            "text_for_embedding": child["text_for_embedding"],
                            "token_count": child["token_count"],
                            "chunk_index": child["chunk_index"],
                            "total_chunks_in_parent": child["total_chunks_in_parent"],
                            "metadata": child["metadata"],
                        },
                    )
                )

            client.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)
            progress.advance(upsert_task, len(batch))

    info = client.get_collection(COLLECTION_NAME)
    log.info("upsert_complete", total_points=info.points_count)


def _chunk_id_to_uuid(chunk_id: str) -> str:
    """Convert our string chunk_id to a deterministic UUID Qdrant requires.

    Qdrant point IDs must be integers or UUIDs. We use UUID v5 (name-based)
    for stable IDs derived from chunk_id strings.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
