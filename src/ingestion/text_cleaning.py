"""Text normalization utilities for German legal PDFs.

Each function is pure: input string -> output string. No side effects.
"""

import unicodedata

import regex as re

LIGATURE_MAP = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
}


def fix_ligatures(text: str) -> str:
    for lig, replacement in LIGATURE_MAP.items():
        text = text.replace(lig, replacement)
    return text


def fix_hyphenation(text: str) -> str:
    """Rejoin words split across line breaks: 'Daten-\\nschutz' -> 'Datenschutz'."""
    return re.sub(r"-\n(\p{Ll})", r"\1", text)


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def remove_page_artifacts(text: str) -> str:
    """Strip standalone page numbers and repeated headers."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"-?\s*S?\.?\s*\d{1,4}\s*-?", stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def clean_text(text: str) -> str:
    """Full cleaning pipeline. Apply in order."""
    text = normalize_unicode(text)
    text = fix_ligatures(text)
    text = fix_hyphenation(text)
    text = remove_page_artifacts(text)
    text = normalize_whitespace(text)
    return text
