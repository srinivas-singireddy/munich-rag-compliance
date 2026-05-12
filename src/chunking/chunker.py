"""Hierarchical chunker producing parent-child chunk pairs.

Strategy:
    1. Each Section from Day 2 becomes one ParentChunk (or, if very long,
       splits into multiple ParentChunks).
    2. Each ParentChunk is split into ChildChunks of ~CHILD_TARGET_TOKENS,
       respecting sentence boundaries.
    3. Each ChildChunk is prefixed with document/section context before
       being stored as text_for_embedding.
"""

from __future__ import annotations

import hashlib

from src.chunking.models import (
    ChildChunk,
    ChunkLevel,
    ChunkMetadata,
    ChunkingStats,
    ParentChunk,
)
from src.chunking.tokenization import count_tokens, split_into_sentences
from src.ingestion.models import Document, Section
from src.logging_setup import get_logger

log = get_logger(__name__)

# Tunable knobs — production systems treat these as config
CHILD_TARGET_TOKENS = 200  # ideal child chunk size
CHILD_MAX_TOKENS = 256  # hard cap (well below 512 model limit)
CHILD_OVERLAP_SENTENCES = 1  # sentence overlap between consecutive children
PARENT_MAX_TOKENS = 1500  # split parents larger than this
PARENT_TARGET_TOKENS = 1000  # ideal parent size
MIN_CHUNK_TOKENS = 30  # sections smaller than this get merged with neighbors


def _stable_id(*parts: str) -> str:
    """Deterministic short ID from input parts."""
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def _make_context_prefix(metadata: ChunkMetadata) -> str:
    """Build the context prefix prepended to each child chunk before embedding.

    Format: '[DSGVO · Artikel 5 · Grundsätze für die Verarbeitung]\\n\\n'
    The brackets and middle-dots make it visually distinct from body text;
    the embedding model learns to use both this prefix and the body for retrieval.
    """
    pieces: list[str] = [metadata.doc_title]
    if metadata.section_heading:
        # Strip any leading markdown artifacts
        pieces.append(metadata.section_heading.lstrip("#* ").rstrip("*").strip())
    return f"[{' · '.join(pieces)}]\n\n"


def _build_metadata(doc: Document, section: Section | None) -> ChunkMetadata:
    """Construct the metadata bundle attached to each chunk."""
    if section is not None:
        return ChunkMetadata(
            doc_id=doc.doc_id,
            doc_title=doc.title,
            doc_type=doc.doc_type.value,
            language=doc.language.value,
            section_id=section.section_id,
            section_heading=section.heading,
            section_number=section.section_number,
            section_numbers=section.section_numbers,  # ← forward the new field
            section_type=section.section_type,
            page_start=section.page_start,
            page_end=section.page_end,
        )
    return ChunkMetadata(
        doc_id=doc.doc_id,
        doc_title=doc.title,
        doc_type=doc.doc_type.value,
        language=doc.language.value,
        page_start=1,
        page_end=doc.page_count,
    )


def _split_into_child_chunks(
    text: str,
    language: str,
    parent_id: str,
    metadata: ChunkMetadata,
    target_tokens: int = CHILD_TARGET_TOKENS,
    max_tokens: int = CHILD_MAX_TOKENS,
    overlap_sentences: int = CHILD_OVERLAP_SENTENCES,
) -> list[ChildChunk]:
    """Split a parent's text into child chunks, respecting sentence boundaries.

    Token-counting is done on the FINAL embedding text (prefix + body), not
    estimated from buffer math. This is the only way to guarantee max_tokens
    is respected, because tokenization is not additive across string boundaries.
    """
    sentences = split_into_sentences(text, language)
    if not sentences:
        return []

    prefix = _make_context_prefix(metadata)
    chunks: list[ChildChunk] = []

    def emit_chunk(body_sentences: list[str]) -> None:
        """Emit a chunk; if it's too big, hard-split on tokens as a last resort."""
        if not body_sentences:
            return
        raw_text = " ".join(body_sentences)
        text_for_embedding = prefix + raw_text
        token_count = count_tokens(text_for_embedding)

        # If still too big after sentence packing, hard-split on tokens
        if token_count > max_tokens:
            from src.chunking.tokenization import get_tokenizer

            tokenizer = get_tokenizer()
            prefix_token_ids = tokenizer.encode(prefix, add_special_tokens=False)
            body_token_ids = tokenizer.encode(raw_text, add_special_tokens=False)
            body_window = max_tokens - len(prefix_token_ids) - 5  # safety margin
            if body_window < 50:  # pathological prefix; skip prefix this time
                body_window = max_tokens - 5
                use_prefix = ""
            else:
                use_prefix = prefix

            for start in range(0, len(body_token_ids), body_window):
                window = body_token_ids[start : start + body_window]
                sub_text = tokenizer.decode(window, skip_special_tokens=True).strip()
                final_text = use_prefix + sub_text
                final_tokens = count_tokens(final_text)
                chunk_index = len(chunks)
                chunks.append(
                    ChildChunk(
                        chunk_id=f"c_{parent_id}_{chunk_index:03d}",
                        level=ChunkLevel.CHILD,
                        parent_id=parent_id,
                        text_for_embedding=final_text,
                        text_raw=sub_text,
                        token_count=final_tokens,
                        chunk_index=chunk_index,
                        total_chunks_in_parent=0,  # backfilled later
                        metadata=metadata,
                    )
                )
            return

        # Normal path — chunk fits within max_tokens
        chunk_index = len(chunks)
        chunks.append(
            ChildChunk(
                chunk_id=f"c_{parent_id}_{chunk_index:03d}",
                level=ChunkLevel.CHILD,
                parent_id=parent_id,
                text_for_embedding=text_for_embedding,
                text_raw=raw_text,
                token_count=token_count,
                chunk_index=chunk_index,
                total_chunks_in_parent=0,
                metadata=metadata,
            )
        )

    # Greedy sentence-packing pass
    buffer: list[str] = []

    def buffer_token_count() -> int:
        if not buffer:
            return 0
        return count_tokens(prefix + " ".join(buffer))

    for sentence in sentences:
        # Try adding the sentence
        candidate = buffer + [sentence]
        candidate_tokens = count_tokens(prefix + " ".join(candidate))

        if candidate_tokens <= target_tokens:
            # Comfortably fits within target — keep packing
            buffer = candidate
        elif candidate_tokens <= max_tokens and not buffer:
            # Buffer was empty; this single sentence fits within max but exceeds target.
            # Emit as standalone chunk.
            emit_chunk([sentence])
            buffer = []
        elif buffer:
            # Adding this sentence would exceed target; flush current buffer first.
            emit_chunk(buffer)
            # Start new buffer with overlap from previous
            overlap = buffer[-overlap_sentences:] if overlap_sentences > 0 else []
            buffer = list(overlap) + [sentence]
            # If overlap + new sentence already exceeds max, drop the overlap
            if count_tokens(prefix + " ".join(buffer)) > max_tokens:
                buffer = [sentence]
        else:
            # Empty buffer + sentence already too big — must hard-split
            emit_chunk([sentence])
            buffer = []

    # Flush remainder
    if buffer:
        emit_chunk(buffer)

    # Backfill total_chunks_in_parent
    total = len(chunks)
    for c in chunks:
        c.total_chunks_in_parent = total

    return chunks


def _hard_split_sentence(sentence: str, max_tokens: int) -> list[str]:
    """Last-resort splitter for sentences that exceed max_tokens.

    Splits on tokens, not characters, and re-decodes to text. Rare in practice
    but prevents pipeline crashes on pathological input.
    """
    from src.chunking.tokenization import get_tokenizer

    tokenizer = get_tokenizer()
    token_ids = tokenizer.encode(sentence, add_special_tokens=False)
    pieces: list[str] = []
    for i in range(0, len(token_ids), max_tokens):
        window = token_ids[i : i + max_tokens]
        pieces.append(tokenizer.decode(window, skip_special_tokens=True).strip())
    return [p for p in pieces if p]


def _section_to_parents(doc: Document, section: Section) -> list[tuple[str, str, ChunkMetadata]]:
    """Convert one section into one-or-more (parent_id, parent_text, metadata) tuples.

    Most sections become a single parent. Very long sections are split into
    multiple parents along sentence boundaries.
    """
    metadata = _build_metadata(doc, section)
    full_text = section.text.strip()
    if not full_text:
        return []

    section_tokens = count_tokens(full_text)
    parent_id_seed = f"{doc.doc_id}_{section.section_id}"

    if section_tokens <= PARENT_MAX_TOKENS:
        parent_id = f"p_{_stable_id(parent_id_seed)}"
        return [(parent_id, full_text, metadata)]

    # Split this section into multiple parents
    sentences = split_into_sentences(full_text, doc.language.value)
    parents: list[tuple[str, str, ChunkMetadata]] = []
    buffer: list[str] = []
    buffer_tokens = 0
    part_index = 0

    def emit() -> None:
        nonlocal buffer, buffer_tokens, part_index
        if not buffer:
            return
        text = " ".join(buffer)
        parent_id = f"p_{_stable_id(parent_id_seed, str(part_index))}"
        parents.append((parent_id, text, metadata))
        part_index += 1
        buffer = []
        buffer_tokens = 0

    for sentence in sentences:
        st = count_tokens(sentence)
        if buffer and (buffer_tokens + st) > PARENT_TARGET_TOKENS:
            emit()
        buffer.append(sentence)
        buffer_tokens += st
    emit()

    return parents


def chunk_document(
    doc: Document, stats: ChunkingStats
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    """Chunk one document into parents and children.

    Falls back to whole-document chunking if no sections were detected at extraction.
    """
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []

    sections_to_process = doc.sections
    if not sections_to_process:
        log.warning("no_sections_falling_back", doc_id=doc.doc_id)
        stats.fallback_text_chunked += 1
        # Treat the entire document as one virtual section
        sections_to_process = [
            Section(
                section_id="full_doc",
                heading=doc.title,
                section_number=None,
                section_type="other",
                text=doc.full_markdown,
                page_start=1,
                page_end=doc.page_count,
                char_count=len(doc.full_markdown),
            )
        ]

    # Merge tiny adjacent sections so we don't create 30-token parents
    merged_sections = _merge_tiny_sections(sections_to_process, doc.language.value)

    for section in merged_sections:
        section_was_split = False
        section_parents = _section_to_parents(doc, section)
        if len(section_parents) > 1:
            section_was_split = True

        for parent_id, parent_text, metadata in section_parents:
            parent_tokens = count_tokens(parent_text)

            # Generate children
            section_children = _split_into_child_chunks(
                parent_text, doc.language.value, parent_id, metadata
            )

            # If section is short enough, emit one child equal to the parent
            if not section_children:
                continue

            parents.append(
                ParentChunk(
                    chunk_id=parent_id,
                    level=ChunkLevel.PARENT,
                    text=parent_text,
                    token_count=parent_tokens,
                    metadata=metadata,
                    child_ids=[c.chunk_id for c in section_children],
                )
            )
            children.extend(section_children)

        if section_was_split:
            stats.sections_split += 1
        else:
            stats.sections_with_no_split += 1

    stats.documents_processed += 1
    stats.parent_chunks_created += len(parents)
    stats.child_chunks_created += len(children)
    return parents, children


def _merge_tiny_sections(sections: list[Section], language: str) -> list[Section]:
    """Merge consecutive sections smaller than MIN_CHUNK_TOKENS into their neighbor.

    Prevents emitting 30-token parents that hurt retrieval signal-to-noise.
    """
    if not sections:
        return []

    merged: list[Section] = []
    buffer: Section | None = None

    for section in sections:
        section_tokens = count_tokens(section.text)
        if section_tokens < MIN_CHUNK_TOKENS:
            if buffer is None:
                buffer = section
            else:
                # Merge into buffer
                buffer = Section(
                    section_id=buffer.section_id,
                    heading=buffer.heading,
                    section_number=buffer.section_number,
                    section_type=buffer.section_type,
                    text=buffer.text + "\n\n" + section.text,
                    page_start=buffer.page_start,
                    page_end=section.page_end,
                    char_count=buffer.char_count + section.char_count,
                )
            continue

        # Normal-sized section
        if buffer is not None:
            # Merge buffer into this section as preamble
            section = Section(
                section_id=section.section_id,
                heading=section.heading,
                section_number=section.section_number,
                section_type=section.section_type,
                text=buffer.text + "\n\n" + section.text,
                page_start=buffer.page_start,
                page_end=section.page_end,
                char_count=buffer.char_count + section.char_count,
            )
            buffer = None
        merged.append(section)

    if buffer is not None:
        merged.append(buffer)

    return merged
