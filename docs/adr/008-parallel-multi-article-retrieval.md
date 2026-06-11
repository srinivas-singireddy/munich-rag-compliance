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

### 5. ThreadPoolExecutor Worker Cap Strategy (Bounded Static Ceiling)

We use a bounded thread allocation strategy max_workers=min(len(article_refs), _MAX_WORKERS) with a hard safety ceiling of _MAX_WORKERS = 8, rather than an unbounded dynamic pool (len(article_refs)).

During testing with a 6-article data subject rights query (Articles 15, 16, 17, 18, 20, and 21), an explicit hard cap of _MAX_WORKERS = 5 caused a severe concurrency bottleneck. The first 5 threads executed immediately, while art_21 was forced into a blocked queue for 4,135ms, only starting after the initial batch completed. This eliminated true parallel I/O and introduced massive, unnecessary tail latency to the request.

To resolve this queuing issue, two scaling models were evaluated:

* Option A (Bounded Static Ceiling - Chosen): We selected a capped allocation 
  strategy using a maximum safety ceiling of 8. Real-world compliance and 
  multi-reference regulatory queries naturally top out at 6 to 8 article 
  references. Raising the ceiling to 8 fully accommodates complex legal queries 
  without artificial queuing, while providing a rigid structural barrier against 
  resource exhaustion.

* Option B (Dynamic Sizing - Rejected): This model scales automatically to match 
  the input request size. While highly flexible, Option B is completely unbounded. 
  It presents a critical security vulnerability: a malformed query or an 
  adversarial denial-of-service (DoS) attack referencing a massive string of 
  regulations (e.g., "Compare Art. 1 through Art. 50 DSGVO") would force the 
  application runtime to abruptly spawn dozens of native OS threads, triggering 
  extreme CPU context-switching overhead and risking taking down the entire 
  service container.


By combining the two approaches into min(len(article_refs), _MAX_WORKERS), 
the system optimizes hardware footprint dynamically for small queries while 
safely flattening adversarial inputs to a predictable maximum — as chosen 
in Option A.

### 6. Hard Metadata Filtering over Sub-Query-Only Partitioning
We use a strict, pre-filtered Qdrant field condition mapping key="metadata.section_id" to enforce strict article isolation per thread, rather than relying on natural embedding proximity via the sub-query string alone.

During edge-case testing using a non-existent regulatory reference (art_99), an architecture relying purely on sub-query construction (f"Artikel 99 DSGVO") failed to isolate the query scope. Instead of returning an empty array [] as expected for a missing corpus element, the retrieval engine returned five chunks from Article 9 (art_9). Because numerical tokens like "99" and "9" sit in close proximity within dense embedding vector spaces, pure semantic search suffers from context bleed—inadvertently pulling adjacent legal paragraphs when a precise article number has no direct indexing representation.

To enforce deterministic structural boundaries, two scoping strategies were evaluated:

Option B (Sub-Query Only Scoping): This model relies entirely on the embedding model and cross-encoder reranker to naturally cluster results around the text tokens. While functional for heavily populated, distinctive legal articles, it breaks down completely on short, procedural, or missing articles where semantic vector boundaries overlap. This allows false-positive text chunks to bleed into the context pipeline.

Option A (Hard Qdrant Metadata Filtering - Chosen): This model introduces explicit database-level pre-filtering. By dissecting the underlying Qdrant collection payload, we identified that the exact article tracking identifiers are deeply nested within the database schema at payload.metadata.section_id (with the raw text similarly mapped to payload.text_raw).

By building an explicit structural filter constraint:

article_filter = Filter(
    must=[FieldCondition(
        key="metadata.section_id",
        match=MatchValue(value=article_ref)
    )]
)

and injecting it directly into the atomic retrieve() payload execution, we convert the search pipeline from approximately correct to semantically and structurally exact.

If an article is missing or possesses no substantive corpus records (such as art_99), the database-level pre-filter cleanly halts context pollution and drops the search space to zero, returning a mathematically accurate empty list [] instead of bleeding wrong-article text into the downstream context assembler.

### 7. Sequential Inference Locking for Thread-Unsafe Local Rerankers

We use a module-level synchronization lock (threading.Lock()) to serialize the reranking phase across worker threads as a tactical hardening measure, rather than splitting the retrieval and reranking pipelines into separate architectural components immediately.

During stress testing of the dual-article query ("Compare Art. 28 and Art. 29 DSGVO"), the system encountered a critical Apple Silicon memory allocator crash: malloc: *** error for object 0x6000387c2c10: pointer being freed was not allocated.

This crash occurred because the newly implemented hard metadata pre-filters optimized the initial Qdrant database fetches to be so fast that both threads completed their I/O tasks simultaneously and hit the local reranker (BAAI/bge-reranker-v2-m3) at the exact same microsecond. The underlying deep learning framework executing on the MacBook's unified memory space (via MPS/Metal) is inherently thread-unsafe during concurrent inference, causing race conditions in memory allocation pointers and triggering immediate segmentation faults.

To eliminate this inference crash while preserving parallel retrieval, two mitigation paths were evaluated:

Option B (Split Fetch and Rerank - Strategic Target): This model restricts parallel worker threads to pure Qdrant vector fetches (strategy="hybrid"). All retrieved candidates are subsequently aggregated and run through a single, unified reranking pass against the original user query outside the thread pool. This architecture is cleaner and scientifically superior because the cross-encoder scores all global candidates collectively rather than scoring isolated article sub-pools. However, implementing Option B requires a major refactoring of the internal search logic across multiple modules.

Option A (In-Thread Mutex Locking - Chosen for Day 10 Hardening): This model introduces a rigid module-level mutex lock (_reranker_lock = threading.Lock()) directly within the concurrent worker loops. The lock wraps the atomic retrieve() execution:

with _reranker_lock:
    results = retrieve(
        client=client,
        query=sub_query,
        strategy="hybrid+rerank",
        top_k=top_k,
        filter_=article_filter,
    )


By enforcing a strict execution traffic light, background threads continue to perform heavy, slow network I/O calls to Qdrant completely in parallel. The moment they transition to local model compute, they seamlessly wait in line, executing their inference passes one at a time. This completely blocks thread-safety pointer violations on Apple Silicon hardware while requiring minimal code footprint modifications.

Option A effectively hardens the runtime immediately for current milestones. 

The more comprehensive pipeline separation detailed in Option B is deferred as a formal design consequence to be executed during the scheduled optimization window.


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
- Hard metadata filter eliminates article bleed — art_99 query correctly 
  returns 0 substantive chunks instead of 5 art_9 chunks
- Bounded max_workers ceiling protects against adversarial query resource 
  exhaustion — 50-ref query capped at 8 threads, not 50
- Reranker lock eliminates Apple Silicon malloc crash under concurrent inference

**Negative / Trade-offs:**
- Token cost increases linearly with article count — three articles = three
  reranker passes. Acceptable for ≤5 articles; may need batching for larger
  queries
- `ThreadPoolExecutor` adds OS thread overhead vs. async coroutines. Acceptable
  at current scale; revisit on EKS (ADR-007)
- Art. 29 citation coverage remains low by design — the article is genuinely
  64 tokens in the DSGVO corpus. No retrieval strategy can compensate for
  absent source content
- _reranker_lock serialises reranker inference — parallel threads now wait 
  on reranking, reducing speedup from theoretical 6× to effective ~5× on 
  6-article queries (wall_ms observed: 4,804ms vs ~28,800ms sequential)
- Option B (parallel fetch + single rerank) deferred to Days 11–13 — 
  current lock is a tactical fix, not the clean architecture






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
- L-015: Classifier routes natural-language topic queries as simple_rag
- L-016: max_workers cap caused art_21 to queue on 6-article query  
- L-017: Qdrant payload field path is metadata.section_id (nested)
- L-018: Reranker thread-safety issue on Apple Silicon; Option B is clean fix