"""Full chunking pipeline orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from src.chunking.chunker import chunk_document
from src.chunking.models import (
    ChildChunk,
    ChunkingStats,
    ParentChunk,
)
from src.ingestion.models import Document
from src.logging_setup import get_logger

log = get_logger(__name__)


def load_document(json_path: Path) -> Document:
    """Load a Day-2-extracted document from JSON."""
    return Document.model_validate_json(json_path.read_text(encoding="utf-8"))


def chunk_corpus(
    processed_dir: Path,
) -> tuple[list[ParentChunk], list[ChildChunk], ChunkingStats]:
    """Chunk every Document JSON in processed_dir."""
    stats = ChunkingStats()
    all_parents: list[ParentChunk] = []
    all_children: list[ChildChunk] = []

    json_files = sorted(processed_dir.glob("*.json"))
    log.info("chunk_corpus_start", document_count=len(json_files))

    for json_path in json_files:
        # Skip output files from this very pipeline (idempotency)
        if json_path.stem.startswith(("chunks_", "chunking_stats")):
            continue
        try:
            doc = load_document(json_path)
        except Exception as e:
            log.error("load_failed", file=json_path.name, error=str(e))
            continue

        log.info("chunking", doc_id=doc.doc_id, sections=len(doc.sections))
        parents, children = chunk_document(doc, stats)
        all_parents.extend(parents)
        all_children.extend(children)
        log.info(
            "chunked",
            doc_id=doc.doc_id,
            parents=len(parents),
            children=len(children),
        )

    # Compute aggregates
    if all_parents:
        stats.avg_parent_tokens = sum(p.token_count for p in all_parents) / len(all_parents)
    if all_children:
        stats.avg_child_tokens = sum(c.token_count for c in all_children) / len(all_children)
        stats.max_child_tokens = max(c.token_count for c in all_children)

    return all_parents, all_children, stats


def save_chunks(
    parents: list[ParentChunk],
    children: list[ChildChunk],
    stats: ChunkingStats,
    output_dir: Path,
) -> dict[str, Path]:
    """Write parents, children, and stats as JSON Lines + a summary."""
    output_dir.mkdir(parents=True, exist_ok=True)

    parents_path = output_dir / "chunks_parents.jsonl"
    children_path = output_dir / "chunks_children.jsonl"
    stats_path = output_dir / "chunking_stats.json"

    with parents_path.open("w", encoding="utf-8") as f:
        for p in parents:
            f.write(p.model_dump_json() + "\n")

    with children_path.open("w", encoding="utf-8") as f:
        for c in children:
            f.write(c.model_dump_json() + "\n")

    stats_path.write_text(stats.model_dump_json(indent=2), encoding="utf-8")

    return {
        "parents": parents_path,
        "children": children_path,
        "stats": stats_path,
    }
