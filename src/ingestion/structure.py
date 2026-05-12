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
# Bold-style headings, with optional markdown header prefix (##, ###):
# matches '**Section N**' OR '## **Section N**'
SECTION_BOLD_PATTERN = re.compile(
    r"^#{0,3}\s*\*\*Section\s+(?P<num>\d+[a-z]?)\*\*\s*$\s*#{0,3}\s*\*\*(?P<title>[^\*]+)\*\*",
    re.MULTILINE | re.IGNORECASE,
)

SECTION_BOLD_SIMPLE = re.compile(
    r"^#{0,3}\s*\*\*Section\s+(?P<num>\d+[a-z]?)\*\*\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Markdown-headed Article: '## Artikel 5 Title' OR '## **Artikel 5 Title**'
ARTICLE_PATTERN = re.compile(
    r"^\s*#{1,3}\s*\*{0,2}\s*Artikel?\s+(?P<num>\d+[a-z]?)\s*[:.\-—]?\s*(?P<title>[^\n*]*?)\s*\*{0,2}\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Bare bold Article heading: '**Artikel N Title**' on its own line
ARTICLE_BOLD_PATTERN = re.compile(
    r"^\*\*Artikel?\s+(?P<num>\d+[a-z]?)\s*(?P<title>[^\n*]*?)\*\*\s*$",
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


def _expand_article_numbers(num_text: str) -> list[str]:
    """Expand range or comma-list notation into individual article numbers.

    NOTE (2026-05-12): Currently unused. Designed for multi-article range
    headings, but those turned out to be recital margin annotations, not
    headings. Kept for future recital detection — see L-006.

    Examples:
        '5'           -> ['5']
        '25a'         -> ['25a']
        '13–14'       -> ['13', '14']       (en-dash, common in German PDFs)
        '13-14'       -> ['13', '14']
        '13—14'       -> ['13', '14']       (em-dash)
        '11, 15'      -> ['11', '15']
        '12-14'       -> ['12', '13', '14']
        '11, 15-17'   -> ['11', '15', '16', '17']
    """
    num_text = num_text.strip()
    if not num_text:
        return []

    # Comma-separated lists: recurse on each part
    if "," in num_text:
        result: list[str] = []
        for part in (p.strip() for p in num_text.split(",")):
            result.extend(_expand_article_numbers(part))
        return result

    # Range with dash (-, –, or —)
    range_match = re.match(
        r"^(?P<start>\d+)(?P<start_suffix>[a-z]?)\s*[–—-]\s*(?P<end>\d+)(?P<end_suffix>[a-z]?)$",
        num_text,
        re.IGNORECASE,
    )
    if range_match:
        start_num = int(range_match.group("start"))
        end_num = int(range_match.group("end"))
        # Sanity bound: don't expand unrealistic ranges like '1-99'
        if 0 < end_num - start_num <= 10:
            return [str(n) for n in range(start_num, end_num + 1)]
        # For wider ranges, just keep the endpoints (preserves the data without explosion)
        return [str(start_num), str(end_num)]

    # Single number (with optional letter suffix like '25a')
    single_match = re.match(r"^\d+[a-z]?$", num_text, re.IGNORECASE)
    if single_match:
        return [num_text.lower()]

    # Unknown format — return as-is to avoid silent loss
    return [num_text.lower()]


def detect_sections(markdown: str, pages_text: list[str]) -> list[Section]:
    matches: list[tuple[int, str, str, str]] = []

    # Pass 1: Markdown # headings
    for m in ARTICLE_PATTERN.finditer(markdown):
        matches.append((m.start(), "article", m.group("num"), m.group(0).strip()))

    # Pass 1b: Bare bold Article headings (some DSGVO Articles lack ## prefix)
    for m in ARTICLE_BOLD_PATTERN.finditer(markdown):
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

    # ← NEW DISAMBIGUATION BLOCK GOES HERE ↓

    # === Disambiguate duplicate (type, number) by appending position suffix ===
    sorted_matches = sorted(matches, key=lambda t: t[0])
    seen_counts: dict[tuple[str, str], int] = {}
    unique_matches: list[tuple[int, str, str, str]] = []
    for start, sect_type, num, heading in sorted_matches:
        # Normalize whitespace inside num for dedup comparison
        normalized_num = re.sub(r"\s+", "", num.lower())
        key = (sect_type, normalized_num)
        count = seen_counts.get(key, 0)
        if count > 0:
            suffix = chr(ord("a") + count)  # 'b' for 2nd, 'c' for 3rd
            disambiguated_num = f"{num}_{suffix}"
        else:
            disambiguated_num = num
        seen_counts[key] = count + 1
        unique_matches.append((start, sect_type, disambiguated_num, heading))
    matches = unique_matches
    # === END ===
    # ← THIS LINE GETS REMOVED (the dedup block already sorts) ↓
    # matches.sort(key=lambda t: t[0])    ← DELETE THIS

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
                section_numbers=[num.lower()],  # keep the field; populate with single entry
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
