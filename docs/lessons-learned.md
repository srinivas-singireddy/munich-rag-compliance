# Lessons Learned

Postmortem-style log of concrete issues hit during the project, options
tried, and resolution. Optimized for "tell me about a difficult problem
you solved" interview prompts.

Format per entry:
- **What happened** — symptoms observed.
- **Investigation** — what was tried, what was ruled out.
- **Resolution** — what fixed it.
- **Takeaway** — the generalizable lesson.

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