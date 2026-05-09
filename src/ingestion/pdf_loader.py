"""Extract structured Markdown + sections from PDF files."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pymupdf4llm
from langdetect import DetectorFactory, detect

from src.ingestion.models import Document, DocumentType, Language
from src.ingestion.structure import detect_sections
from src.ingestion.text_cleaning import clean_text
from src.logging_setup import get_logger

DetectorFactory.seed = 42
log = get_logger(__name__)


def detect_language(text: str, sample_chars: int = 5000) -> Language:
    sample = text[:sample_chars].strip()
    if not sample:
        return Language.UNKNOWN
    try:
        code = detect(sample)
        if code == "de":
            return Language.DE
        if code == "en":
            return Language.EN
        return Language.UNKNOWN
    except Exception:
        return Language.UNKNOWN


def classify_doc_type(filename: str, text_sample: str) -> DocumentType:
    fname = filename.lower()
    if any(k in fname for k in ("dsgvo", "bdsg", "regulation")):
        return DocumentType.REGULATION
    if any(k in fname for k in ("bafin", "rundschreiben")):
        return DocumentType.BAFIN_CIRCULAR
    sample = text_sample[:2000].lower()
    if "rundschreiben" in sample:
        return DocumentType.BAFIN_CIRCULAR
    if "verordnung" in sample or "regulation" in sample:
        return DocumentType.REGULATION
    return DocumentType.UNKNOWN


def extract_pdf(pdf_path: Path) -> Document:
    """Extract a single PDF into a structured Document."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    log.info("extracting", file=pdf_path.name)
    warnings: list[str] = []

    # Step 1: Open with PyMuPDF for page count + per-page text
    pages_text: list[str] = []
    try:
        with pymupdf.open(pdf_path) as doc:
            page_count = doc.page_count
            for page in doc:
                pages_text.append(page.get_text() or "")
    except Exception as e:
        log.error("pymupdf_open_failed", file=pdf_path.name, error=str(e))
        raise

    # Step 2: Extract markdown
    try:
        raw_markdown = pymupdf4llm.to_markdown(
            str(pdf_path),
            show_progress=False,
        )
    except Exception as e:
        warnings.append(f"pymupdf4llm failed: {e} — falling back to plain text")
        raw_markdown = "\n\n".join(pages_text)

    # Step 3: Clean
    cleaned_markdown = clean_text(raw_markdown)
    if not cleaned_markdown:
        warnings.append("extraction produced empty text")

    # Step 4: Metadata
    doc_id = pdf_path.stem
    language = detect_language(cleaned_markdown)
    doc_type = classify_doc_type(pdf_path.name, cleaned_markdown)

    # Step 5: Structure detection
    sections = detect_sections(cleaned_markdown, pages_text)
    if not sections:
        warnings.append("no sections detected — chunking will use full text")

    document = Document(
        doc_id=doc_id,
        source_path=str(pdf_path),
        title=doc_id.replace("_", " ").title(),
        doc_type=doc_type,
        language=language,
        page_count=page_count,
        full_markdown=cleaned_markdown,
        sections=sections,
        extraction_warnings=warnings,
    )

    log.info(
        "extracted",
        file=pdf_path.name,
        pages=page_count,
        chars=document.total_chars,
        sections=len(sections),
        language=language.value,
        doc_type=doc_type.value,
        warnings=len(warnings),
    )
    return document


def save_document(document: Document, output_dir: Path) -> Path:
    """Serialize Document to JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document.doc_id}.json"
    output_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return output_path
