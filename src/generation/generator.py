"""Mistral API integration with streaming + citation-grounded generation."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import TYPE_CHECKING

import structlog
from mistralai import Mistral

if TYPE_CHECKING:
    from src.retrieval.hybrid_search import RetrievalResult

log = structlog.get_logger(__name__)

# ── Prompt template ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a German regulatory compliance assistant specialising in DSGVO (GDPR) \
and BDSG. Answer the user's question using ONLY the provided source excerpts. \
Rules:
1. Ground every claim in a specific article/section — cite as [Art. X DSGVO] \
or [§ Y BDSG].
2. If the excerpts do not contain enough information, say so explicitly.
3. Reply in the same language as the question (German question → German answer, \
English question → English answer).
4. Be concise and precise; this is a legal domain.\
"""

USER_TEMPLATE = """\
## Source excerpts
{context}

## Question
{question}

## Answer (with inline citations)\
"""


def _build_context(parents: list[dict]) -> str:
    """Format parent chunks into a numbered context block."""
    blocks: list[str] = []
    for i, p in enumerate(parents, 1):
        meta = p.get("metadata", {})
        source = meta.get("source_doc", "unknown")
        heading = meta.get("section_heading", "")
        page = meta.get("page_start", "?")
        text = p.get("text", "").strip()
        label = f"[{i}] {heading} (p. {page}, {source})"
        blocks.append(f"{label}\n{text}")
    return "\n\n---\n\n".join(blocks)


# ── Public API ────────────────────────────────────────────────────────────────


class ComplianceGenerator:
    """Wraps Mistral client; exposes generate() and stream()."""

    def __init__(self, api_key: str | None = None, model: str = "mistral-small-latest") -> None:
        key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise ValueError("MISTRAL_API_KEY not set and no api_key passed")
        self.client = Mistral(api_key=key)
        self.model = model
        log.info("generator.ready", model=self.model)

    def _messages(self, question: str, parents: list[dict]) -> list[dict]:
        context = _build_context(parents)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(context=context, question=question)},
        ]

    def generate(self, question: str, parents: list[dict]) -> str:
        """Blocking call — returns full answer string."""
        resp = self.client.chat.complete(
            model=self.model,
            messages=self._messages(question, parents),
        )
        answer = resp.choices[0].message.content
        log.info("generator.done", chars=len(answer))
        return answer

    def stream(self, question: str, parents: list[dict]) -> Generator[str, None, None]:
        """Yields text deltas for Streamlit st.write_stream()."""
        with self.client.chat.stream(
            model=self.model,
            messages=self._messages(question, parents),
        ) as stream:
            for event in stream:
                delta = event.data.choices[0].delta.content
                if delta:
                    yield delta
