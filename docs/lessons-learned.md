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

This is exactly the kind of bug that ships to production and quietly degrades
RAG quality for months without anyone noticing. Catching it pre-Day-4 prevented
a class of "embeddings look fine but retrieval recall is mysteriously low"
problems we'd have spent days debugging in Week 2.


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

What had been `## Artikel 5` style headings in earlier runs became
`## **Artikel 5 Grundsätze...**` (combined `##` + bold + title on one line)
after the cleaning change. The original `ARTICLE_PATTERN` no longer matched.
Section count crashed from 20 to 4 for DSGVO.

**Resolution — three layers of defense**

1. **Upstream: disambiguate duplicates in `structure.py`.** Sections with
   identical `(type, number)` get position-suffixed IDs (`art_13`, `art_13_b`,
   `art_13_c`). Both physical sections preserved with unique IDs.
2. **Middle: natural parent IDs in `chunker.py`.** Removed MD5 truncation
   entirely. Parents now use `p_{doc_id}_{section_id}` — human-readable and
   collision-impossible because the inputs are already unique. Bonus: easier
   debugging since chunk IDs are readable.
3. **Bottom: reconciliation in `embed_and_index.py`.** Pipeline now compares
   input chunk count to indexed point count and warns loudly on mismatch.
   This is what caught the original bug.

Also widened `ARTICLE_PATTERN` and `SECTION_BOLD_PATTERN` to tolerate
markdown-heading prefixes (`#{0,3}`) and optional bold wrappers (`\*{0,2}`)
so future extraction drift doesn't silently regress detection.

Final state: 178 sections detected (vs. 106 before), 642 well-aligned children
indexed, every chunk corresponds to exactly one Article. Counterintuitively
fewer chunks than before but with substantially better semantic coherence.

**Takeaway — three compounding lessons**

- **Reconcile input to output, always.** Every batch pipeline should compare
  N_in to N_out and alert loudly on mismatch. This is the cheapest,
  highest-leverage defense against silent data loss. Without it, we'd have
  shipped 0.36% data loss to "production."

- **Truncated hashes are rarely worth the bytes saved.** Modern stores handle
  long IDs efficiently. Birthday-paradox collisions on truncated MD5 are not
  theoretical — they hit at small N when inputs are non-uniform. If a string
  is already unique (doc_id + section_id), don't hash it.

- **Text pipelines have cascading state.** A change in one cleaning step
  can silently reshape what downstream stages see, even though the upstream
  library itself is deterministic. Defense: tolerant patterns, golden
  extraction fixtures in CI, count-based smoke tests, content-hash logging
  per stage.

In production at a German bank, this exact failure pattern would have
produced *intermittent retrieval gaps* — Article 13 queries returning Article
14 content for the first user, Article 12 for the second, depending on which
parent's children won the upsert race. Months of degraded answers before
someone noticed a pattern in complaints. The reconciliation check turned a
silent multi-month outage into a five-minute pre-deployment fix.


## L-006: PDF margin annotations mistaken for headings

**Date:** 2026-05-12
**Phase:** Day 5 prep — post-Day-4 review

**What happened**
During post-Day-4 code review, noticed that several DSGVO chunks had been
tagged with section IDs like `art_13_b`, `art_13_c`, suggesting duplicate
section detection. Looking at the source PDF revealed headings like
`Artikel 13–14`, `Artikel 11, 15`, `Artikel 30–31`. Initial interpretation:
range/list headings that should produce multi-article tagging.

Designed a fix: regex patterns extended to capture ranges, a
`_expand_article_numbers` helper to expand `13–14` into `['13', '14']`, and
a `section_numbers: list[str]` field on `Section` and `ChunkMetadata` for
multi-article membership.

**Investigation**
After applying the fix, extraction crashed from 178 sections to 1. Initial
debugging chased regex greediness and `\s` matching newlines. Then inspection
of the actual PDF pages revealed the real issue: these "headings" are not
headings at all. They are **margin annotations** in a multi-column legal
layout — left-margin cross-references attached to *recitals*
(Erwägungsgründe), pointing to which articles each recital explains.

The DSGVO has three distinct structural elements:
- Articles (Artikel 1–99): binding legal provisions
- Recitals ((1)–(173)): explanatory rationale, not legally binding
- Cross-references: margin annotations linking recitals to articles

`pymupdf4llm` linearizes the multi-column layout, interleaving margin text
with main column text. Margin annotations occasionally surface as standalone
lines that look like headings but aren't.

The "Artikel 13–14" duplicate detected on Day 4 was the same phenomenon —
not a duplicate Article heading, but a marginalia fragment masquerading as
one. Compounded by a Python indentation bug introduced during the manual
revert (`return sections` indented inside the for-loop), the regression
cascaded from 178 → 4 → 1 sections.

**Resolution**
Reverted regex changes back to single-article matching. Fixed the
indentation bug that emerged during revert. Kept the model fields
(`section_numbers`, `_expand_article_numbers` helper, Qdrant payload index)
because they're harmless, populated with single-entry lists today, and
forward-compatible for future recital work.

Final state: 178 sections, 642 reconciled child chunks, identical to Day 4's
known-good baseline.

**Proper future approach (deferred, not implemented)**
1. Detect recitals (`(N)` numbered paragraphs in DSGVO) as their own sections
   with `section_type="recital"`.
2. Extract margin-annotation article references via spatial PDF parsing
   (PyMuPDF bounding boxes) since `pymupdf4llm` linearization loses the
   column structure.
3. Populate `section_numbers` on recitals with the articles each annotates.
4. Add bidirectional retrieval: query for Article N → also surface recitals
   that annotate it.

Deferred because spatial PDF parsing for marginalia is non-trivial — requires
custom PyMuPDF code reading bounding boxes per page, plus heuristics linking
column-1 annotations to column-2 paragraphs. Several days of work, tangential
to the RAG architecture this project showcases.

**Takeaways**

*Three lessons compound here:*

- **Look at the actual document, not just the extracted text.** L-003 was
  about inspecting extracted markdown before writing regex. L-006 is the
  next level — inspect the source PDF layout itself. Half a day was lost
  designing a solution for a problem that didn't exist; five minutes
  looking at PDF pages would have prevented it.

- **Python indentation is silently load-bearing.** A four-space shift on
  `return sections` turned a 178-section function into a 1-section
  function. Code ran without error. Lint tools don't catch it. Defense:
  visual review of indentation after any manual edit to functions with
  nested control flow.

- **Scope boundaries protect velocity.** Recital detection is genuine
  domain depth, but not what differentiates this project. Reverting and
  shipping with strong article-level retrieval beats chasing PDF-layout
  completeness at the cost of the broader portfolio narrative. Senior
  engineering is partly about knowing what to *not* solve right now.

## L-007: Reranker required text_for_embedding not text_raw — silent quality collapse

**Date:** 2026-05-13
**Phase:** Day 5 — Hybrid search evaluation

**What happened**
Initial hybrid+rerank evaluation produced P@1=33% — far worse than dense
baseline of 79%. The reranker was actively demoting correct results.

**Investigation**
The reranker (`bge-reranker-v2-m3`) scores `(query, chunk_text)` pairs.
Initial implementation passed `text_raw` — the chunk body without the
context prefix. For citation queries like "Artikel 83 Absatz 4", the raw
chunk body contains the article's *content* but not the article's *number*.
The prefix `[Dsgvo Official De · Artikel 83 Allgemeine Bedingungen...]`
contains "Artikel 83" — exactly what the query is looking for — but the
reranker never saw it.

Result: the reranker saw no connection between "Artikel 83" in the query
and the content of an Article 83 chunk. It penalised these chunks and
promoted lower-ranked but apparently more "relevant" chunks.

**Resolution**
Changed reranker input from `text_raw` to `text_for_embedding` (which
includes the context prefix). P@1 immediately restored to 79% — matching
the dense baseline. The section heading in the prefix provides the
structural signal the reranker needs to connect citation queries to the
right article.

**Also discovered:** BM25/bm42 sparse retrieval provides minimal value
on conceptual German legal queries due to synonym richness. "Strafen"
(penalties in query) vs "Geldbußen" (fines in corpus) have zero token
overlap — sparse search adds noise. Hybrid helps on exact citation
queries but hurts on conceptual ones. The reranker rescues the hybrid
regressions on conceptual queries by re-scoring semantically.

**Takeaway**
The reranker input must match what was indexed — if chunks were indexed
with prefixes, the reranker must see those same prefixes. Otherwise the
reranker's relevance judgements are made on a different text representation
than what the retrieval model used, creating a consistency gap.

More broadly: in multi-stage retrieval pipelines, every stage must operate
on *consistent text representations*. Changing the text between stages
silently degrades quality in ways that are hard to attribute without a
careful evaluation harness.  

## L-008: mistralai SDK v2.x broke `from mistralai import Mistral`

**Date:** 2026-05-14  
**Phase:** Day 6 — Generation integration

### What happened
`uv add mistralai>=1.0.0` resolved to `mistralai==2.4.5` (latest).
`from mistralai import Mistral` raised `ImportError: cannot import name 'Mistral'`
despite the class existing in v1.x. The v2.x SDK reorganised the package structure
and the top-level `Mistral` import path changed.

### Root cause
mistralai did a breaking API reorganisation between v1.x and v2.x with no
deprecation shim. `uv add mistralai>=1.0.0` satisfied the constraint with v2.4.5,
silently pulling in the incompatible version.

### Fix
Pin explicitly in pyproject.toml:
`mistralai==1.2.5`

### Takeaway
For any SDK that has crossed a major version boundary recently, always pin to
the exact major.minor you verified against. `>=1.0.0` is not safe when v2.x
exists. Check PyPI history before writing `>=` constraints on fast-moving
AI provider SDKs (mistralai, openai, anthropic, cohere all have form here).

### Upgrade path
Before upgrading to v2.x: check mistralai changelog for the new import path,
update generator.py accordingly, re-run `uv run python -c 'from mistralai
import Mistral; print("ok")'` before touching anything else.