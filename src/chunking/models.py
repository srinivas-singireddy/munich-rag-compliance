"""Data models for the chunking pipeline.

Two-level hierarchy:
    - ParentChunk: large unit (full section or merged sections), used for LLM generation
    - ChildChunk: small unit (~200 tokens), embedded and indexed for retrieval
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ChunkLevel(StrEnum):
    PARENT = "parent"
    CHILD = "child"


class ChunkMetadata(BaseModel):
    """Metadata travelling with every chunk through the pipeline.

    This is what flows into the vector store payload and ultimately reaches
    the LLM as citation context.
    """

    doc_id: str
    doc_title: str
    doc_type: str
    language: str

    # Source section (from Day 2 extraction)
    section_id: str | None = None
    section_heading: str | None = None
    section_number: str | None = None
    section_type: str | None = None

    # Page range — for citations back to the original PDF
    page_start: int
    page_end: int


class ParentChunk(BaseModel):
    """Large chunk, retrieved and sent to the LLM."""

    chunk_id: str = Field(..., description="Deterministic ID, e.g. 'p_dsgvo_art_5'")
    level: ChunkLevel = ChunkLevel.PARENT
    text: str = Field(..., description="Raw text — no context prefix needed")
    token_count: int = Field(..., ge=0)
    metadata: ChunkMetadata
    child_ids: list[str] = Field(default_factory=list)


class ChildChunk(BaseModel):
    """Small chunk, embedded and indexed for retrieval."""

    chunk_id: str = Field(..., description="Deterministic ID, e.g. 'c_dsgvo_art_5_002'")
    level: ChunkLevel = ChunkLevel.CHILD
    parent_id: str = Field(..., description="ID of the parent chunk")

    # The text we EMBED (includes context prefix)
    text_for_embedding: str
    # The clean text without prefix (for display/debugging)
    text_raw: str

    token_count: int = Field(..., ge=0, description="Token count of text_for_embedding")
    chunk_index: int = Field(..., ge=0, description="0-based index within the parent")
    total_chunks_in_parent: int = Field(..., ge=0)

    metadata: ChunkMetadata


class ChunkingStats(BaseModel):
    """Pipeline statistics — useful for the summary report."""

    documents_processed: int = 0
    parent_chunks_created: int = 0
    child_chunks_created: int = 0
    avg_parent_tokens: float = 0.0
    avg_child_tokens: float = 0.0
    max_child_tokens: int = 0
    sections_with_no_split: int = 0
    sections_split: int = 0
    fallback_text_chunked: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
