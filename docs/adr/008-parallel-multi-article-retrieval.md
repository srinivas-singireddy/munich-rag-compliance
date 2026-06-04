# ADR-008: Parallel Multi-Article Retrieval

**Date:** 2026-06-04
**Status:** Accepted
**Deciders:** Srinivas Singireddy
**Context:** Days 9–10 — Agent Orchestration (Parallel Retrieval)

---

## Context

The Day 8 agent graph classified queries as `simple_rag`, `multi_article`, or
`out_of_scope`, but the `multi_article` path was a stub — it sent a single
`retrieve()` call with the full query string and hoped all referenced articles
surfaced. For a query like "Compare Art. 28 and Art. 29 DSGVO on processor
obligations", a single hybrid search returned only Art. 28 chunks because the
topic ("processor obligations") semantically dominates the embedding space for
that article. Art. 29 — a 64-token, one-sentence article — was consistently
missed.

For complex multi-article queries (e.g. "What do Art. 5, Art. 13, and Art. 14
say about transparency?"), a single retrieval call may miss one or more target
articles entirely, producing hallucinated citations and low confidence scores.

The solution is to dispatch one `retrieve()` call per article reference
concurrently, then merge results before passing to the existing
`context_assembler` node.

---

## Decision

### 1. ThreadPoolExecutor over asyncio

We use `concurrent.futures.ThreadPoolExecutor` to parallelise `retrieve()` calls,
not `asyncio`.

`retrieve()` is a synchronous, blocking function that calls the Qdrant sync
client and then the Mistral reranker over HTTP. If we used `asyncio`, we would
be forced to run every database request sequentially anyway — `asyncio`
achieves concurrency only when the underlying I/O clients are async-native
(e.g. `AsyncQdrantClient`, `httpx`). Wrapping sync blocking calls in
`asyncio.run_in_executor` adds complexity with no benefit.

`ThreadPoolExecutor` delegates each blocking `retrieve()` call to an independent
OS thread. When a thread blocks waiting for network packets, the OS switches
execution to another thread — true parallel I/O with zero changes to the
existing `retrieve()` internals. This allows us to reuse our tested,
optimised retrieval pipeline as a black box.

Migration to async clients is deferred to Days 17–21 when the EKS deployment
introduces async-native infrastructure requirements.

### 2. Article-Only Sub-Query Format

When dispatching a retrieval thread for a specific article reference, we build
the sub-query as:

```python
sub_query = f"Artikel {article_num} DSGVO"
```

We deliberately avoid appending the full user query text, and we do not
hardcode topic keywords.

Our retrieval pipeline separates two concerns across two stages:

- **Stage 1 — Retrieval (Article Scoping):** The sole responsibility of the
  initial vector/keyword search is to fetch chunks belonging to the target
  article. Appending the user's full query injects semantic noise that dilutes
  the article number signal. For example, a query about "data processor
  obligations regarding data breaches" would cause the Art. 28 thread to pull
  data breach chunks from Art. 33 or Art. 34 instead of staying scoped to
  Art. 28. The clean format `Artikel {article_num} DSGVO` guarantees the
  retrieval step fetches the correct legal paragraphs.

- **Stage 2 — Reranking (Topic Scoring):** Once article-scoped chunks are
  retrieved, the cross-encoder reranker scores them against the original full
  user query. Because cross-encoders model nuanced semantic relationships, the
  reranker surfaces the exact paragraphs within the article that match the
  user's specific intent — without contaminating the retrieval step with that
  intent prematurely.

Hardcoding topic keywords (e.g. "Pflichten Inhalt") was considered and rejected
because it biases every sub-query toward obligation-style content regardless of
what the user asked, degrading retrieval for rights, breach notification, or
consent queries.

### 3. Module-Level Model Warmup Before ThreadPoolExecutor

All models used by the retrieval pipeline — dense encoder, sparse encoder, and
cross-encoder reranker — are warmed up sequentially at module import time,
before any `ThreadPoolExecutor` is initialised:

```python
# src/agent/parallel_retriever.py
from src.embeddings.encoder import get_dense_model, get_sparse_model
from src.retrieval.hybrid_search import get_reranker

get_dense_model()
get_sparse_model()
get_reranker()
```

All three use `@lru_cache(maxsize=1)`. When warmed at import time, background
threads reference the same cached model instance in memory — no redundant
loading, no data copying overhead.

If models are initialised lazily inside threads, two threads hitting the
`@lru_cache` boundary simultaneously during model loading produce a tensor
dtype race condition (`Half` vs `Float`), causing one thread to fail with a
matrix multiplication error. This was observed in the first smoke test run
(Day 9) and resolved by moving warmup to module level.

### 4. Merge Strategy — Best Score Wins, Not Average

After all parallel threads complete, chunks from all sub-queries are merged:

- Deduplicate by `chunk_id` — a chunk may appear in multiple threads
- Keep the highest score if a chunk appears more than once
- Sort merged list by score descending
- Pass to `context_assembler` unchanged

Averaging scores was considered and rejected for two reasons:

**Averaging dilutes critical relevance signals.** A chunk scoring 0.9 in one
thread (hyper-relevant to that article) and 0.2 in another (unrelated thread)
averages to 0.55 — ranking below a chunk scoring 0.6 in both threads (average
0.60), even though the first chunk is an absolute match for at least one
targeted article. Best score preserves the maximum signal; averaging penalises
a chunk for failing a query it was never meant to answer.

**Alignment with LLM context ordering.** LLMs suffer from the "lost in the
middle" phenomenon — attention is highest at the beginning of the context
window. Sorting by best score ensures the most relevant chunks occupy the
highest-priority positions in the prompt. Average scoring would suppress
high-signal chunks into the middle of the context, reducing answer quality.

---

## Consequences

**Positive:**
- Multi-article queries now retrieve distinct, article-scoped chunks per
  reference — zero chunk overlap observed across threads for clean sub-queries
- Confidence for three-article queries improved from 0.528 to 0.806 in smoke
  testing (Test 2, Day 9)
- Hallucinated citations dropped from 2 to 0 for the same query after
  sub-query format fix
- Parallel execution: three concurrent threads complete in ~3 seconds vs ~9
  seconds sequential
- Zero changes to existing `retrieve()`, `context_assembler`, `generator`, or
  `citation_validator` nodes

**Negative / Trade-offs:**
- Token cost increases linearly with article count — three articles = three
  reranker passes. Acceptable for ≤5 articles; may need batching for larger
  queries
- `ThreadPoolExecutor` adds OS thread overhead vs. async coroutines. Acceptable
  at current scale; revisit on EKS (ADR-007)
- Art. 29 citation coverage remains low by design — the article is genuinely
  64 tokens in the DSGVO corpus. No retrieval strategy can compensate for
  absent source content

---

## Alternatives Considered

| Alternative | Reason Rejected |
|---|---|
| Single retrieve() with full query for multi_article | Misses articles whose topic is dominated by a co-referenced article |
| asyncio with run_in_executor | Adds wrapper complexity; no benefit over ThreadPoolExecutor for sync clients |
| Sub-query = `Art. N + full query` | Topic semantics dominate article number; threads return overlapping wrong-article chunks |
| Hardcoded topic keywords in sub-query | Biases all queries toward obligation-style content; degrades rights/breach/consent queries |
| Average score merge | Dilutes high-signal chunks; misaligns with LLM context ordering requirements |

---

## Related

- ADR-003: Hybrid search + reranker (production retrieval strategy)
- ADR-006: LangGraph agent orchestration (graph topology this extends)
- ADR-007: Qdrant EKS deployment (async migration deferred to here)
- L-013: Model warmup must precede ThreadPoolExecutor
- L-014: Sub-query scope determines retrieval precision