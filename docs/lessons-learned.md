# Lessons Learned

Postmortem-style log of concrete issues hit during the project, options
tried, and resolution. Optimized for "tell me about a difficult problem
you solved" interview prompts.

Format per entry:
- **What happened** — symptoms observed.
- **Investigation** — what was tried, what was ruled out.
- **Resolution** — what fixed it.
- **Takeaway** — the generalizable lesson.

## Index

| # | Issue | Takeaway |
|---|-------|----------|
| L-001 | BaFin JS-rendered scraper returned 0 PDFs | Good-enough corpus beats perfect corpus you can't get |
| L-002 | `onnxruntime` Apple Silicon wheel conflict | Platform override + `--no-deps` pattern for ML dependencies |
| L-003 | BDSG 0 sections — wrong heading format assumed | Look at the data before writing the regex |
| L-004 | Token overflow despite hard-cap enforcement | Tokenization is not additive across string boundaries |
| L-005 | Silent data loss — three-layer root cause | Reconcile input/output counts; no hash truncation; tolerant patterns |
| L-006 | PDF margin annotations mistaken for headings | Inspect source PDF layout; Python indentation is silently load-bearing |
| L-007 | Reranker required text_for_embedding not text_raw | Multi-stage pipelines need consistent text representations across stages |
| L-008 | mistralai v2.x broke `from mistralai import Mistral` | Pin exact major.minor for fast-moving AI SDKs |
| L-009 | `[tool.uv.env]` doesn't exist; bare `python` bypasses venv | Always use `uv run python`; use `.env` for PYTHONPATH |
| L-010 | `RetrievalResult` fields differ from Qdrant `ScoredPoint` | Always grep the actual return type before wrapping existing functions |
| L-011 | Parent `chunk_id` is top-level in `chunks_parents.jsonl`, not nested under `metadata` | Run `head -1` on jsonl files and print key structure before writing any lookup logic |
| L-012 | `uv run pip` is not venv-aware on this setup | Always use `importlib.metadata` for package introspection |
| L-013 | Model warmup must precede ThreadPoolExecutor | Warm all `@lru_cache` models at module import time before spawning threads |
| L-014 | Sub-query scope determines retrieval precision | Article-scoped retrieval + topic-scoped reranking — never conflate the two |
| L-015 | Classifier routes natural-language topic queries as simple_rag even when answer spans multiple articles | multi_article activates on explicit article refs only; topic-spanning queries need a different detection strategy — candidate for Days 11–13 eval harness |
| L-016 | 15 of 25 golden records had no `expected_doc_id` — scoring silently accepted either law | Optional fields read with `.get()` fail open; backfill and assert completeness when a field is added mid-dataset; measure suspected inflation, don't assume it |
| L-017 | fastembed BM42 cache in macOS `$TMPDIR` — model silently re-downloaded | Library cache defaults are dev conveniences; pin every model cache path and test offline |
| L-018 | Qdrant RRF ties returned in arbitrary order — hybrid metrics not reproducible | Fetch the full fused set, sort locally with a stable tie-break; deterministic ≠ correct; CI gates compare thresholds, never exact lists |

---

## L-001: BaFin Rundschreiben scraper returned zero PDFs despite live URLs

**Date:** 2026-04-30
**Phase:** Day 1 — Corpus acquisition

**What happened**
The Day 1 scraper successfully fetched the BaFin Rundschreiben hub page,
discovered 10 sub-pages, fetched each one — and extracted zero PDF links.
Initial Direct downloads (DSGVO Berlin, BDSG English) succeeded.

**Investigation**
Two issues found in sequence:
1. EUR-Lex returned `HTTP 202 Accepted` with `content-type: text/html` for the
   PDF endpoint — an anti-scraping pattern returning a "preparing your PDF"
   page rather than the binary. Adding `content-type` validation revealed it.
2. BaFin's hub page is rendered server-side, but its actual PDF links sit
   inside JavaScript-loaded tables. `httpx` only sees the static shell.
   Headless browser (Playwright) would solve it but adds 200MB and 30 minutes
   of fragility.

**Resolution**
Pragmatic call: dropped EUR-Lex (Berlin's DSGVO PDF was already a higher-quality
source); accepted BaFin scraping as best-effort and shipped with two
high-quality regulatory PDFs (~140 pages combined). Documented the JS-rendering
limitation rather than fighting it.

**Takeaway**
Production scrapers always have fallback layers, and "good enough corpus" beats
"complete corpus you can't get." Junior engineers spend days fighting BaFin's
JS rendering. Senior engineers recognise the boundary and ship.

---

## L-002: `onnxruntime` Apple Silicon wheel resolution conflict

**Date:** 2026-04-30
**Phase:** Day 2 — Adding dependencies

**What happened**
`uv sync` failed with: `Distribution onnxruntime==1.26.0 can't be installed
because it doesn't have a source distribution or wheel for the current
platform`. Listed wheels covered Linux x86_64/arm64 and Windows — but not
macOS arm64. PyPI manually showed macOS arm64 wheels existed for 1.26.0,
suggesting a transitive-dependency-driven resolution mismatch rather than a
true wheel absence.

**Investigation**
Tried four approaches:
1. Pin `onnxruntime>=1.26.0` explicitly — same failure.
2. Add `[tool.uv] environments = [...]` platform declaration — same failure.
3. Install `pymupdf4llm --no-deps` and let `uv` skip onnx — broke `pymupdf4llm`
   imports because `tabulate` was a real runtime dependency.
4. Add `[tool.uv] override-dependencies = ["onnxruntime ; sys_platform == 'linux'"]`
   to skip on macOS entirely — succeeded.

**Resolution**
Pinned `pymupdf4llm` install via `--no-deps`, then manually added back its
real runtime dependency `tabulate`. Configured `override-dependencies` to make
`onnxruntime` Linux-only, since our pipeline doesn't actually invoke it.

**Takeaway**
ML Python on Apple Silicon hits this class of issue routinely. The senior pattern
is: 12-factor resolve-locally-deploy-on-Linux via Docker is the only durable fix.
For local dev, `override-dependencies` is the right escape hatch. Documented this
because the same conflict will happen again on the next ML project.

---

## L-003: BDSG section detection returned zero — wrong heading format assumed

**Date:** 2026-05-09
**Phase:** Day 2 — Structure detection

**What happened**
After implementing structural section detection for `# Artikel N` (DSGVO style)
and `# § N` (BDSG German style), running on the corpus produced:
- DSGVO: 20 sections detected ✅
- BDSG English: 0 sections detected ❌

Both PDFs had clearly section-segmented content. Regex was returning empty.

**Investigation**
Initial reflex was to add an `^# Section N` pattern and re-run. Resisted it.
Instead, dumped the first 3000 characters of `bdsg_official_en.json`'s
`full_markdown` and inspected. Discovered `pymupdf4llm` had emitted section
headings as `**Section 1**` (markdown bold), not `# Section 1` (markdown header).
The PDF was generated from Word with bold formatting acting visually as headings
but lacking true heading style metadata.

**Resolution**
Added two new regex patterns: `SECTION_BOLD_PATTERN` (matches the two-line
`**Section N**\n**Title**` form) and `SECTION_BOLD_SIMPLE` (single-line fallback).
Re-run produced 86 sections — exactly matching the BDSG's 85 actual sections plus
the document title.

**Takeaway**
**Look at the data before writing the pattern.** Five minutes of inspection saved
30 minutes of guessed-pattern iteration. Different PDF generators (Word vs. LaTeX
vs. InDesign) produce different markdown extraction outputs even from semantically
identical documents. Real-world legal corpora always include all three.

---

## L-004: Chunk token-overflow despite hard-cap enforcement

**Date:** 2026-05-10
**Phase:** Day 3 — Hierarchical chunking

**What happened**
First chunk pipeline run produced 33 chunks exceeding the 256-token hard cap,
maximum 466 tokens. The chunker had explicit `CHILD_MAX_TOKENS = 256` and a
`_hard_split_sentence` safety net that should have caught oversized sentences.
On Day 4 these would have been silently truncated by the embedding model,
degrading retrieval quality without obvious symptoms.

**Investigation**
Three iterations to find the real cause:

1. **First fix attempt:** Added `effective_max = max_tokens - prefix_tokens` to
   account for prefix overhead. Reduced max from 466 → 370 but didn't eliminate.

2. **Inspected actual offending chunks.** Most started with text like
   `"a) die betroffene Person bereits über die Informationen verfügt; b)..."`.
   These were not multiple sentences — the German `sentence-splitter` correctly
   identified them as single sentences containing internal enumerations. The
   sentences themselves were 250-370 tokens.

3. **Second fix attempt:** Tightened the hard-split trigger to fire at 80%
   of effective max. Still didn't work — max stayed at 368.

4. **Root cause identified:** Tokenization is **not additive** across string
   boundaries. `count_tokens(prefix) + count_tokens(body) ≠ count_tokens(prefix + body)`
   because subword tokenization at the boundary produces extra tokens. The chunker's
   buffer math was estimating size based on summed components, then producing
   final embeddings whose actual token count exceeded the cap.

**Resolution**
Rewrote `_split_into_child_chunks` to **measure final tokens, not estimate from
buffer math**. Every flush decision now calls `count_tokens(prefix + " ".join(buffer))`
on the actual final string. Added a guaranteed-safe `emit_chunk` function:
if a chunk somehow still exceeds `max_tokens` after sentence packing, it
hard-splits on token IDs and decodes back to text. By construction, every
emitted chunk is ≤ max_tokens.

Result: 836 children, max 256 tokens, zero warnings.

**Takeaway**
**Tokenization is not additive across string boundaries.** Any chunking algorithm
that estimates final token count from component sums has a silent bug waiting
to surface. The correct pattern is "measure-then-decide": always tokenize the
actual final string at every decision point. Slightly slower (more tokenizer
calls), but mathematically guaranteed.

---

## L-005: Silent data loss caught by reconciliation — three-layer root cause

**Date:** 2026-05-11
**Phase:** Day 4 — Embedding + Qdrant indexing

**What happened**
After embedding 836 child chunks and upserting to Qdrant, the collection
reported 833 indexed points. No error, no warning — the pipeline reported
success. Investigation revealed three nested bugs.

**Investigation — Layer 1: ID collisions**
Compared input chunk_id count (836) to unique chunk_id count (833) before
upsert. Three duplicate IDs: `c_p_41ba9fb993_000/_001/_002`, all children
of the same parent. First hypothesis: MD5-truncated parent IDs colliding via
birthday paradox.

**Investigation — Layer 2: Duplicate section_ids**
Drilled deeper: which parents shared ID `p_41ba9fb993`? Found two distinct
Section objects in the DSGVO, both with `section_id = "art_13"`. The
parent-ID hash wasn't colliding — the *inputs* to the hash were
identical. The bug was upstream in section detection.

**Investigation — Layer 3: PDF extraction drift**
Inspected the DSGVO markdown. The PDF presents some articles under combined
range headers like `Artikel 13–14` that appear on multiple consecutive pages
as navigation aids. Our regex matched both occurrences, both producing
`section_id = "art_13"`. Adding deduplication broke other extractions —
revealing that `pymupdf4llm` output had also drifted between runs because
adding `text.replace("\xad", "")` to `text_cleaning.py` reshaped line breaks
upstream, changing which markdown heading variants downstream regex saw.

**Resolution**
Three layers of defence added. Final state: 178 sections, 642 reconciled
child chunks, every chunk corresponds to exactly one Article.

**Takeaway**
Reconcile input to output counts at every batch pipeline stage. Truncated
hashes create birthday-paradox collisions at small N. Text pipelines have
cascading state — a cleaning change upstream can silently reshape what
downstream stages see.

---

## L-006: PDF margin annotations mistaken for headings

**Date:** 2026-05-12
**Phase:** Day 5 prep — post-Day-4 review

**What happened**
Several DSGVO chunks tagged with `art_13_b`, `art_13_c` suggested duplicate
section detection. Designed a range-expansion fix. After applying it,
extraction crashed from 178 sections to 1.

**Investigation**
These "headings" are not headings — they are margin annotations in a
multi-column legal layout, left-margin cross-references linking recitals to
articles. `pymupdf4llm` linearizes the multi-column layout, interleaving
margin text with main column text. A Python indentation bug introduced during
manual revert (`return sections` indented inside the for-loop) compounded the
regression from 178 → 4 → 1 sections.

**Resolution**
Reverted regex changes. Fixed indentation bug. Final state: 178 sections,
642 child chunks, identical to Day 4 known-good baseline.

**Takeaway**
Inspect the source PDF layout, not just the extracted text. Python indentation
is silently load-bearing — a four-space shift on `return statements` produces
no error but completely changes behaviour. Scope boundaries protect velocity:
recital detection is genuine domain depth but not what differentiates this
project.

---

## L-007: Reranker required text_for_embedding not text_raw

**Date:** 2026-05-13
**Phase:** Day 5 — Hybrid search evaluation

**What happened**
Initial hybrid+rerank evaluation produced P@1=33% — far worse than dense
baseline of 79%. The reranker was actively demoting correct results.

**Investigation**
The reranker scored `(query, chunk_text)` pairs using `text_raw` — the chunk
body without the context prefix. For citation queries like "Artikel 83 Absatz 4",
the raw chunk body contains the article's content but not its number. The prefix
`[Dsgvo Official De · Artikel 83 ...]` contains "Artikel 83" but the reranker
never saw it.

**Resolution**
Changed reranker input from `text_raw` to `text_for_embedding`. P@1 immediately
restored to 79%.

**Takeaway**
In multi-stage retrieval pipelines, every stage must operate on consistent text
representations. If chunks were indexed with prefixes, the reranker must see
those same prefixes. Changing text between stages silently degrades quality.

---

## L-008: mistralai SDK v2.x broke `from mistralai import Mistral`

**Date:** 2026-05-14
**Phase:** Day 6 — Generation integration

**What happened**
`uv add mistralai>=1.0.0` resolved to `mistralai==2.4.5`. `from mistralai import
Mistral` raised `ImportError` — v2.x reorganised the package structure with no
deprecation shim.

**Resolution**
Pin explicitly: `mistralai==1.2.5`.

**Takeaway**
Pin exact major.minor for fast-moving AI provider SDKs. `>=1.0.0` is not safe
when v2.x exists. Check PyPI history before writing `>=` constraints on
mistralai, openai, anthropic, cohere.

---

## L-009: `[tool.uv.env]` does not exist — bare `python` silently bypasses venv

**Date:** 2026-05-14
**Phase:** Day 7 — Evaluation scripting

**What happened**
`ModuleNotFoundError: No module named 'src'` when running scripts directly.
Attempted to fix via `[tool.uv.env]` in `pyproject.toml` — uv 0.11.7 rejected
it: `unknown field 'env'`. Bare `python` silently picked up system interpreter.

**Resolution**
Create `.env` with `PYTHONPATH=.` — uv loads it automatically on every
`uv run` invocation. Always invoke as `PYTHONPATH=. uv run python`, never
bare `python`.

**Takeaway**
`uv run python` is not optional. Bare `python` is silent failure mode. Do not
infer uv config schema from analogy with pip or poetry — uv's config surface
is smaller and more opinionated.

---

## L-010: `RetrievalResult` fields differ from Qdrant `ScoredPoint`

**Date:** 2026-05-21
**Phase:** Day 8 — Agent orchestration

**What happened**
The `retriever` node in `nodes.py` was written assuming `retrieve()` returned
Qdrant `ScoredPoint` objects with `.id` and `.payload` fields — the standard
Qdrant client return type. At runtime, attribute access failed immediately:
`AttributeError: 'RetrievalResult' object has no attribute 'id'`.

**Investigation**
`retrieve()` in `hybrid_search.py` returns the project's own `RetrievalResult`
dataclass, not raw Qdrant objects. The function wraps Qdrant results and
exposes its own field names: `.chunk_id`, `.parent_id`, `.score`, `.text_raw`,
`.metadata`. These were defined on Day 5 but assumed away on Day 8 when
writing the agent layer on top.

**Resolution**
Grepped `hybrid_search.py` for the `RetrievalResult` definition, read the
actual field names, updated the serialisation block in `retriever` node to
use `.chunk_id`, `.parent_id`, `.metadata.get(...)` accordingly.

**Takeaway**
Always grep the actual return type before wrapping existing functions. Never
assume field names from framework conventions — a function named `retrieve()`
returning Qdrant results does not guarantee it returns raw Qdrant types.
In multi-layer codebases, internal wrapper types accumulate; the wrapper's
field names are the contract, not the underlying library's.

---

## L-011: Parent `chunk_id` is top-level in `chunks_parents.jsonl`, not nested under `metadata`

**Date:** 2026-05-21
**Phase:** Day 8 — Agent orchestration, context_assembler node

**What happened**
`context_assembler` built a lookup dict from `chunks_parents.jsonl` using
`p["metadata"]["chunk_id"]` as the key. All lookups returned `None` —
no parent chunks were assembled, generator received empty context, answers
were fabricated entirely from model weights.

**Investigation**
Printed `list(p.keys())` and `list(p["metadata"].keys())` for the first line
of the jsonl file. `chunk_id` is a top-level key on the parent object.
`metadata` contains `doc_id`, `section_id`, `section_heading`, `doc_title` —
not `chunk_id`. The assumption that IDs live under metadata was wrong.

**Resolution**
Changed lookup key from `p["metadata"]["chunk_id"]` to `p["chunk_id"]`.
Context assembly immediately worked — parent chunks populated correctly.

**Takeaway**
Run `head -1` on jsonl files and print the full key structure before writing
any lookup logic against disk data. Data first, code second. A two-line
inspection would have prevented this entirely. The same principle applies to
any schema you didn't personally write: API responses, database rows, Qdrant
payloads — always verify the actual shape before writing field accessors.

---

## L-012: `uv run pip` is not venv-aware on this setup

**Date:** 2026-05-21
**Phase:** Day 8 — Dependency verification

**What happened**
`uv run pip show langgraph` and `uv run pip check` reported package versions
inconsistent with what was actually importable in the project. Verification
commands were giving false confidence about the venv state.

**Investigation**
On this setup, `uv run pip` routes to the system pip, not the uv-managed
virtualenv. The venv pip and system pip are reporting on different package
sets. A package installed via `uv add` appears in the venv but not in
`uv run pip show` output.

**Resolution**
Use `importlib.metadata` for all package introspection:
```python
PYTHONPATH=. uv run python -c \
  'import importlib.metadata; print(importlib.metadata.version("langgraph"))'
```
This runs inside the uv venv and reports the version actually importable by
the project.

**Takeaway**
Never use `uv run pip` as a health signal on this setup. `importlib.metadata`
is the only reliable introspection method. More broadly: verify package state
using the same interpreter that will run the code — not a side-channel tool
that may be pointed at a different environment.

---

## L-013: Model warmup must precede ThreadPoolExecutor

**Date:** 2026-06-04
**Phase:** Day 9 — Parallel multi-article retrieval

**What happened**
First smoke test of parallel retrieval dispatched two threads simultaneously.
The `art_29` thread failed immediately:
```
error='mat1 and mat2 must have the same dtype, but got Half and Float'
```
The `art_28` thread succeeded. Both threads had hit `get_dense_model()` at
the same timestamp, triggering concurrent model initialisation.

**Investigation**
`get_dense_model()`, `get_sparse_model()`, and `get_reranker()` all use
`@lru_cache(maxsize=1)`. The cache is populated on the first call. When two
threads call a cached function simultaneously before the cache is populated,
both enter the loader concurrently. The `SentenceTransformer` loader is not
thread-safe during weight loading — concurrent access produces a tensor dtype
mismatch (`Half` from one thread's partial load vs `Float` from the other's
completed load) that crashes matrix multiplication at inference time.

The second smoke test added warmup for the dense and sparse models but not
the reranker — the reranker loaded twice (once per thread) in the following
run, confirming the same race applies to all three cached loaders.

**Resolution**
Added sequential warmup of all three models at module import time in
`parallel_retriever.py`, before any `ThreadPoolExecutor` is initialised:
```python
get_dense_model()
get_sparse_model()
get_reranker()
```
After warmup, threads reference the same cached instance in memory — no
concurrent loading, no dtype mismatch.

**Takeaway**
Any `@lru_cache` model loader is not thread-safe during its first call.
Warm all models sequentially at module import time before spawning threads.
This is unconditional — not just when you expect concurrent access. The
rule: if a function uses `@lru_cache` and will be called from multiple
threads, it must be called once on the main thread first.

---

## L-014: Sub-query scope determines retrieval precision

**Date:** 2026-06-04
**Phase:** Day 9 — Parallel multi-article retrieval

**What happened**
Initial parallel retrieval used `f"Art. {article_num} {base_query}"` as the
sub-query for each thread. For a three-article query about transparency
obligations (Art. 5, Art. 13, Art. 14), the Art. 5 thread returned zero
Art. 5 chunks — all five results were Art. 13 and Art. 14 chunks. `art_5`
was flagged as a hallucinated citation despite being explicitly requested.
Confidence: 0.528, hallucinated citations: 2.

**Investigation**
The phrase "transparency and information obligations" in the base query is
semantically dominated by Art. 13 and Art. 14 content — those articles are
literally titled information obligations. When appended to the Art. 5
sub-query, this topic signal overwhelmed the article number prefix in the
embedding space. The reranker then ranked Art. 13/14 chunks above Art. 5
chunks even inside the Art. 5 thread.

A hardcoded topic suffix ("Pflichten Inhalt") was considered and rejected —
it biases every sub-query toward obligation-style content regardless of the
user's actual question, degrading retrieval for rights, breach notification,
or consent queries.

**Resolution**
Changed sub-query format to `f"Artikel {article_num} DSGVO"` — article
reference only, no topic context. The retrieval step scopes to the correct
article; the reranker scores those chunks against the original full query.
Two separate concerns, two separate steps.

Result: 15 distinct chunks (zero overlap across threads), all three articles
validated, zero hallucinations, confidence 0.806.

**Takeaway**
Retrieval and reranking handle two separate concerns and must not be conflated:
- **Retrieval:** Article scoping — find chunks belonging to the target article.
  Sub-query must be clean and article-focused.
- **Reranking:** Topic scoring — score those chunks against the user's intent.
  Full query belongs here, not in the retrieval step.

Injecting topic context into the retrieval sub-query lets semantic noise
override the article identity signal. The retriever finds the wrong article;
the reranker has no way to recover because it only sees what retrieval returned.

## L-016: 15 of 25 golden records had no `expected_doc_id` — scoring silently accepted either law

**Date:** 2026-11-20
**Phase:** Day 11 — golden records had no `expected_doc_id - Backfilled

**What happened**
The Day 11 resume planned to backfill one golden record (Q11) that was missing
`expected_doc_id`. A full-file check found 14 more (Q01–Q03, Q05, Q07–Q10, Q12,
Q14–Q18): only 11 of 25 records were document-scoped. Document scoping was
introduced partway through building the golden set; Q13 and Q19–Q25 carry the
key from the start, Q04/Q06 were backfilled earlier, the rest never were.

**Investigation**
`eval_retrieval.py` reads the key with `item.get("expected_doc_id", None)`. A
missing key becomes `None` and the document check is skipped
(`if expected_doc: ...`), so a BDSG chunk with a matching `section_id` would
count as a hit — DSGVO and BDSG share the `art_N` namespace. Retrieval itself is
never document-filtered; only scoring is affected.

Measured instead of assumed: hybrid+rerank scored three ways (as committed /
Q11 fixed / all 25 scoped) gave 20/25, 22/25, MRR 0.8280 in all three, with
zero per-question changes. The lenient branch never fired.

**Resolution**
Reviewed each of the 15 records — all target DSGVO (Q10's own note says "not
BDSG Sec 37"). Backfilled `expected_doc_id: dsgvo_official_de` with a
line-preserving rewrite: exactly 15 lines changed, 24 DSGVO + 1 BDSG (Q23).
Locked figures unchanged — calibration, not manipulation (ADR-005).

**Takeaway**
An optional field read with `.get()` fails open: a missing key silently turns a
strict check into a lenient one. When a schema field is added mid-dataset,
backfill and assert completeness in the same change. Follow-up: the harness
should warn or fail when `expected_doc_id` is missing.

## L-017: fastembed BM42 cache in macOS `$TMPDIR` — model silently re-downloaded

**Date:** 2026-11-20
**Phase:** Day 11 — model silently re-downloaded

**What happened**
During an eval run, "Fetching 6 files … Download complete: 91.7MB" appeared:
the BM42 sparse model was re-downloaded from the Hugging Face Hub.

**Investigation**
`fastembed_cache` was found under `$TMPDIR` (`/var/folders/…/T/`). macOS cleans
this folder periodically (exact interval unverified), so the model disappears
and is fetched again on next use. The run therefore needed internet access —
contradicting the local / EU-sovereign claim — and would add a cold download to
every CI run and container start.

**Resolution**
Fixed on 2026-11-25 (D3, config externalisation). `get_sparse_model()` now passes
`cache_dir=settings.model_cache_dir / "fastembed"` (default
`~/.cache/munich-rag/models/fastembed`). The existing 87 MB cache was copied over.
Verified with `HF_HUB_OFFLINE=1`: BM42 loads and the full retrieval eval runs with
zero downloads (P@1 80%, R@5 88%, MRR 0.828 unchanged). The Hugging Face models
(dense, reranker, tokenizer) already use the permanent `~/.cache/huggingface`;
containers set `HF_HOME` to a mounted volume (D11).

**Takeaway**
Library defaults for caches are dev conveniences, not deployment decisions. Pin
every model cache path explicitly and test offline.

## L-018: Qdrant RRF ties returned in arbitrary order — hybrid metrics not reproducible

**Date:** 2026-11-20
**Phase:** Day 11 — Qdrant RRF ties returned in arbitrary order

**What happened**
A determinism check showed result lists differing between identical runs.
Q12 (hybrid+rerank): an `art_58` chunk was present in some runs and absent in
others. Plain hybrid: the same golden set gave P@1 16/25 then 17/25 (MRR 0.7233
vs 0.7433). The README hybrid row (0.723) was one sample from that spread.

**Investigation**
Reranker scores were identical to 7 decimals across runs — the reranker is
deterministic. Fusion runs server-side (`FusionQuery(fusion=Fusion.RRF)`); RRF
scores are simple fractions, so exact ties are common. Q06: `art_33` and
`art_83` both scored exactly 0.5 and swapped across runs; a second tie at rank 3
(0.33333334). With `limit=top_k`, the server also cut inside ties at position
40, changing the candidate set the reranker sees.

**Resolution**
`search_hybrid` now requests the full fused set (`limit=2 * top_k`; each
prefetch returns ≤ top_k), sorts locally by `(-score, chunk_id)`, then cuts to
`top_k`. `chunk_id` is an MD5 of `doc_id` + `section_id` (names, not text), so
the order survives index rebuilds. Verified: Q06 identical ×4; hybrid identical
×3 at 16/25, 21/25, MRR 0.7267; hybrid+rerank identical ×2 at 20/25, 22/25,
MRR 0.8280 — locked figures unchanged. See ADR-011.

**Takeaway**
Rank fusion produces exact ties by design; a server that returns ties in
arbitrary order makes metrics non-reproducible. Deterministic is not the same as
correct: in Q06 the tie-break now ranks `art_83` above the correct `art_33`. A CI
gate must compare metrics against thresholds, never exact result lists.



