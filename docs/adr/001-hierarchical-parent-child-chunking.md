# ADR-001: Hierarchical Parent-Child Chunking with Context-Prefix Injection

**Date:** 2026-05-10
**Status:** Accepted
**Deciders:** Srinivas Singireddy

## Context

The Munich RAG project retrieves answers from German regulatory documents
(DSGVO, BDSG) where two opposing forces shape chunking strategy:

1. **Embedding quality favors short, focused text** — a 200-token chunk on
   one specific concept produces a sharp embedding vector; a 2000-token chunk
   spanning multiple concepts produces a fuzzy averaged vector.
2. **LLM answer quality favors broad context** — given only a 200-token snippet,
   the model misses surrounding qualifications, exceptions, and cross-references
   that matter in legal text.

A single chunk size cannot satisfy both. Additionally, the corpus is highly
structured (Articles, Paragraphs, Sections) and German-language, with long
compound words and marathon sentences in DSGVO recitals. Compliance use cases
require auditable citations back to specific sections of the original PDFs.

## Options Considered

### Option 1: Fixed-size chunking with overlap
- **Pros:** Simplest possible; predictable chunk sizes; well-understood.
- **Cons:** Ignores document structure; can split mid-sentence or
  mid-compound-word; produces medium-quality results at both retrieval
  and generation; loses citation granularity.

### Option 2: Structural-only chunking (one chunk per section)
- **Pros:** Respects legal-document structure; clean citation anchors.
- **Cons:** Average section is 600+ tokens — too coarse for sharp embeddings;
  long sections (recitals, complex articles) become unwieldy single chunks
  exceeding embedding model's 512-token limit.

### Option 3: Semantic chunking (embedding-similarity-based)
- **Pros:** Topically coherent chunks; works on unstructured prose.
- **Cons:** Computationally expensive at ingestion; non-deterministic;
  unnecessary for already-structured legal text; hard to debug.

### Option 4: Parent-child hierarchical chunking (small-to-big retrieval)
- **Pros:** Decouples the matching/generation tradeoff. Children optimized
  for retrieval (~200 tokens); parents optimized for generation (full sections).
  Citation metadata flows through both levels. Production-grade pattern used
  by LlamaIndex auto-merging retriever and increasingly expected at senior
  level.
- **Cons:** More complex; doubles storage; requires careful index design.

## Decision

**Adopt Option 4 — hierarchical parent-child chunking — with the following
specifics:**

- **Parents:** Each Section becomes one ParentChunk. Sections exceeding
  1500 tokens are split along sentence boundaries into multiple parents.
  Tiny sections (<30 tokens) merge into neighbors to maintain
  signal-to-noise ratio.
- **Children:** Each parent splits into ChildChunks of ~200 tokens (target),
  with a hard cap of 256 tokens. Sentence boundaries respected via the
  `sentence-splitter` library (German-aware: handles `z.B.`, `Art.`, `Abs.`).
  One sentence of overlap between consecutive children for continuity.
- **Context-prefix injection:** Children carry a metadata prefix of the form
  `[DocTitle · SectionHeading]` prepended to the body before embedding.
  This significantly improves recall (e.g. matches queries mentioning
  "DSGVO" or article numbers even when those terms aren't in the body).
  Parents contain only raw text since the LLM doesn't need it.
- **Token-counted limits enforced post-concatenation:** Token counts are
  measured on `prefix + body` strings, not estimated from buffer math.
  This is the only way to mathematically guarantee `max_tokens` is never
  exceeded, since tokenization is not additive across string boundaries.
- **Hard-split safety net:** Sentences exceeding the effective limit are
  split on token IDs and decoded back to text. Without this, marathon
  DSGVO recital sentences (300+ tokens, common) would crash the pipeline
  or produce silently truncated embeddings.
- **Deterministic chunk IDs:** Generated via MD5 prefix hash of
  `doc_id + section_id + part_index`. Re-chunking is idempotent —
  no duplicate vector store entries on re-runs.
- **JSON Lines output:** Streaming-friendly, append-friendly, standard
  for ML pipelines.

Tunable parameters (in `src/chunking/chunker.py`):

| Parameter | Value | Rationale |
|---|---|---|
| `CHILD_TARGET_TOKENS` | 200 | Sharp embeddings, comfortable buffer below 256 cap |
| `CHILD_MAX_TOKENS` | 256 | Headroom for context-prefix tokens within e5's 512 limit |
| `CHILD_OVERLAP_SENTENCES` | 1 | Continuity at boundaries without excessive duplication |
| `PARENT_TARGET_TOKENS` | 1000 | Comfortable for any modern LLM context window |
| `PARENT_MAX_TOKENS` | 1500 | Forces split for very long articles |
| `MIN_CHUNK_TOKENS` | 30 | Tiny sections merge into neighbors |

## Consequences

**Positive**
- Retrieval recall: context-prefix injection provides structural signal
  the embedder uses to disambiguate queries.
- Generation quality: parents provide rich legal context (full Article
  with all sub-clauses) rather than isolated paragraphs.
- Auditability: every child chunk carries metadata back to source section,
  page range, and document — enabling click-through citations in the UI.
- Pipeline robustness: zero token-overflow warnings on the production
  corpus (164 parents, 836 children, max 256 tokens, 0 warnings).
- Idempotent: re-chunking the same input produces identical chunk IDs.

**Negative**
- Storage doubled compared to flat chunking (~2.2 MB total, negligible).
- Slightly slower ingestion: token counts measured on every flush rather
  than estimated. ~2 seconds added to chunking time on 140-page corpus.
- More complex retrieval logic on Day 5 (children matched, parents returned).
- Adds a third-party dependency (`sentence-splitter`).

**Revisit when:**
- Day 7 evaluation results are available. If retrieval recall < 0.70,
  consider adding hypothetical-question generation per child chunk
  (as previously considered and deferred).
- Corpus expands beyond ~10 documents or ~10,000 chunks. At larger scale,
  storage doubling and retrieval latency may force a reassessment.
- Section detection improves significantly (e.g., for BaFin Rundschreiben).
  May enable finer-grained parent boundaries.

## References

- `src/chunking/chunker.py` — implementation
- `src/chunking/models.py` — data contracts
- `docs/lessons-learned.md` L-004 (to be added) — token-overflow debugging
- LlamaIndex Auto-Merging Retriever pattern (conceptual basis):
  https://docs.llamaindex.ai/en/stable/examples/retrievers/auto_merging_retriever/
- e5 multilingual embedding model:
  https://huggingface.co/intfloat/multilingual-e5-large-instruct