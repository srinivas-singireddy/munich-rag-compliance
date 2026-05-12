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
    """A logical section of a document — typically an Article (Artikel) or §."""

    section_id: str = Field(
        ...,
        description="Stable ID, e.g. 'art_5' or 'art_13_14' for range headings.",
    )
    heading: str = Field(..., description="Full heading text as it appears.")
    section_number: str | None = Field(
        None,
        description="Primary number (first in a range). Kept for backward compatibility.",
    )
    section_numbers: list[str] = Field(
        default_factory=list,
        description=(
            "All article numbers this section applies to. Single-article sections "
            "have one entry; range/list headings like 'Artikel 13-14' have multiple. "
            "This is the field downstream code should use for filtered retrieval."
        ),
    )
    section_type: Literal["article", "paragraph", "recital", "chapter", "other"] = "other"
    text: str = Field(..., description="Body text of the section, cleaned.")
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
