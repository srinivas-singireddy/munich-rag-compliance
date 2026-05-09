"""Detect legal-document structure from extracted Markdown.

Recognises:
    - 'Artikel N' / 'Article N'  (DSGVO style)
    - '§ N'                       (BDSG German style)
    - 'Section N'                 (BDSG English style)
"""

from __future__ import annotations

import regex as re

from src.ingestion.models import Section

# Bold-style headings: **Section N** (common in Word-generated PDFs like BDSG)
SECTION_BOLD_PATTERN = re.compile(
    r"^\*\*Section\s+(?P<num>\d+[a-z]?)\*\*\s*$\n\*\*(?P<title>[^\*]+)\*\*",
    re.MULTILINE | re.IGNORECASE,
)

# Simpler fallback: just **Section N** on its own line
SECTION_BOLD_SIMPLE = re.compile(
    r"^\*\*Section\s+(?P<num>\d+[a-z]?)\*\*\s*$",
    re.MULTILINE | re.IGNORECASE,
)

ARTICLE_PATTERN = re.compile(
    r"^\s*#{1,3}\s*Artikel?\s+(?P<num>\d+[a-z]?)\s*[:.\-—]?\s*(?P<title>.*?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

PARAGRAPH_PATTERN = re.compile(
    r"^\s*#{1,3}\s*§\s*(?P<num>\d+\s*[a-z]?)\s*[:.\-—]?\s*(?P<title>.*?)\s*$",
    re.MULTILINE,
)

# English "Section N" — used in translated German laws (BDSG English version)
SECTION_EN_PATTERN = re.compile(
    r"^\s*#{1,3}\s*Section\s+(?P<num>\d+[a-z]?)\s*[:.\-—]?\s*(?P<title>.*?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

ARTICLE_FALLBACK = re.compile(
    r"^(?P<heading>Artikel?\s+(?P<num>\d+[a-z]?)\s*[—–-]\s*[^\n]+?)$",
    re.MULTILINE | re.IGNORECASE,
)

PARAGRAPH_FALLBACK = re.compile(
    r"^(?P<heading>§\s*(?P<num>\d+\s*[a-z]?)\s*[—–-]\s*[^\n]+?)$",
    re.MULTILINE,
)

SECTION_EN_FALLBACK = re.compile(
    r"^(?P<heading>Section\s+(?P<num>\d+[a-z]?)\s*[—–-]\s*[^\n]+?)$",
    re.MULTILINE | re.IGNORECASE,
)


def detect_sections(markdown: str, pages_text: list[str]) -> list[Section]:
    matches: list[tuple[int, str, str, str]] = []

    # Pass 1: Markdown # headings
    for m in ARTICLE_PATTERN.finditer(markdown):
        matches.append((m.start(), "article", m.group("num"), m.group(0).strip()))

    for m in PARAGRAPH_PATTERN.finditer(markdown):
        matches.append(
            (m.start(), "paragraph", m.group("num").replace(" ", ""), m.group(0).strip())
        )

    for m in SECTION_EN_PATTERN.finditer(markdown):
        matches.append((m.start(), "article", m.group("num"), m.group(0).strip()))

    # Pass 2: Bold-style headings (**Section N**) — Word-generated PDFs
    if not matches:
        # Try two-line pattern first: **Section N**\n**Title**
        for m in SECTION_BOLD_PATTERN.finditer(markdown):
            heading = f"Section {m.group('num')} — {m.group('title').strip()}"
            matches.append((m.start(), "article", m.group("num"), heading))

        # If still nothing, just match **Section N** alone
        if not matches:
            for m in SECTION_BOLD_SIMPLE.finditer(markdown):
                heading = f"Section {m.group('num')}"
                matches.append((m.start(), "article", m.group("num"), heading))

    # Pass 3: Plain-text fallbacks
    if not matches:
        for m in ARTICLE_FALLBACK.finditer(markdown):
            matches.append((m.start(), "article", m.group("num"), m.group("heading").strip()))
        for m in PARAGRAPH_FALLBACK.finditer(markdown):
            matches.append(
                (
                    m.start(),
                    "paragraph",
                    m.group("num").replace(" ", ""),
                    m.group("heading").strip(),
                )
            )
        for m in SECTION_EN_FALLBACK.finditer(markdown):
            matches.append((m.start(), "article", m.group("num"), m.group("heading").strip()))

    matches.sort(key=lambda t: t[0])

    sections: list[Section] = []
    for i, (start, sect_type, num, heading) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        body_lines = body.split("\n", 1)
        text_only = body_lines[1].strip() if len(body_lines) > 1 else ""

        page_start = _char_offset_to_page(start, pages_text)
        page_end = _char_offset_to_page(end - 1, pages_text)

        sections.append(
            Section(
                section_id=f"{sect_type[:3]}_{num.lower()}",
                heading=heading.lstrip("#").strip(),
                section_number=num,
                section_type=sect_type,  # type: ignore[arg-type]
                text=text_only,
                page_start=max(1, page_start),
                page_end=max(1, page_end),
                char_count=len(text_only),
            )
        )

    return sections


def _char_offset_to_page(offset: int, pages_text: list[str]) -> int:
    cumulative = 0
    for i, page in enumerate(pages_text, start=1):
        cumulative += len(page)
        if offset < cumulative:
            return i
    return len(pages_text) or 1
