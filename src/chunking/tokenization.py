"""Token counting and sentence splitting utilities.

We use the actual embedding model's tokenizer for accurate token counts,
and a German-aware sentence splitter to avoid breaking mid-sentence.
"""

from __future__ import annotations

from functools import lru_cache

from sentence_splitter import SentenceSplitter
from transformers import AutoTokenizer

import logging

logging.getLogger("transformers.tokenization_utils_base").setLevel(logging.ERROR)


# This is the tokenizer for the embedding model we'll use on Day 4.
# Loading it now ensures token counts during chunking are exact.
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-large-instruct"


@lru_cache(maxsize=1)
def get_tokenizer():
    """Lazy-load tokenizer. Cached so we only load once per process."""
    return AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)


def count_tokens(text: str) -> int:
    """Exact token count using the embedding model's tokenizer."""
    if not text:
        return 0
    tokenizer = get_tokenizer()
    return len(tokenizer.encode(text, add_special_tokens=False))


@lru_cache(maxsize=4)
def get_sentence_splitter(language: str) -> SentenceSplitter:
    """Lazy-load splitter per language."""
    lang_code = "de" if language == "de" else "en"
    return SentenceSplitter(language=lang_code)


def split_into_sentences(text: str, language: str) -> list[str]:
    """Split text into sentences with language-aware logic.

    For German, this correctly handles abbreviations like 'z.B.', 'Art.', 'Abs.',
    'Nr.' that would fool a naive period-splitter.
    """
    if not text.strip():
        return []
    splitter = get_sentence_splitter(language)
    sentences = splitter.split(text)
    # Drop empty sentences (artifacts of weird formatting)
    return [s.strip() for s in sentences if s.strip()]
