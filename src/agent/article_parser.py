# src/agent/article_parser.py
"""
Extract individual article references from a compliance query string.

Handles:
  "Compare Art. 28 and Art. 29 DSGVO"        → ["art_28", "art_29"]
  "Art. 5, Art. 13, and Art. 14 DSGVO"       → ["art_5", "art_13", "art_14"]
  "What does Art. 5 say?"                     → ["art_5"]
  "BDSG Section 26 and Art. 28"              → ["art_26", "art_28"]
  "What is the weather?"                      → []
"""

import re
from typing import Optional


# Pattern: Art. / Art / Artikel / Article / Section + optional punctuation + digits
_ARTICLE_RE = re.compile(
    r"\b(?:art(?:ikel|icle)?|section|§)\s*[.\s_]*(\d+)\b",
    re.IGNORECASE,
)


def parse_article_refs(query: str) -> list[str]:
    """
    Return deduplicated list of article references in art_N format,
    preserving order of first appearance.
    """
    seen: dict[str, bool] = {}
    results: list[str] = []
    for m in _ARTICLE_RE.finditer(query):
        key = f"art_{m.group(1)}"
        if key not in seen:
            seen[key] = True
            results.append(key)
    return results
