"""Data models for the ingestion pipeline.

These Pydantic models are the contract between extraction (Day 2) and
chunking (Day 3). Every downstream stage consumes/produces these types.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DocumentType(StrEnum):
    REGULATION = "regulation"
    BAFIN_CIRCULAR = "bafin_circular"
    UNKNOWN = "unknown"


class Language(StrEnum):
    DE = "de"
    EN = "en"
    UNKNOWN = "unknown"


class Section(BaseModel):
    section_id: str = Field(..., description="Stable ID e.g. 'art_5' or 'par_25a'")
    heading: str
    section_number: str | None = None
    section_type: Literal["article", "paragraph", "recital", "chapter", "other"] = "other"
    text: str
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    char_count: int = Field(..., ge=0)


class Document(BaseModel):
    doc_id: str
    source_path: str
    title: str
    doc_type: DocumentType = DocumentType.UNKNOWN
    language: Language = Language.UNKNOWN
    page_count: int = Field(..., ge=1)
    full_markdown: str
    sections: list[Section] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    extraction_warnings: list[str] = Field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return len(self.full_markdown)

    @property
    def has_sections(self) -> bool:
        return len(self.sections) > 0
