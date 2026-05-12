# Munich RAG Compliance

A production-grade Retrieval-Augmented Generation (RAG) system over German
regulatory documents (DSGVO + BDSG), built as a portfolio project demonstrating
end-to-end AI engineering for the Munich enterprise market.

**Target audience:** Financial services, insurance, and regulated industries in
Munich/Germany (Allianz, Munich Re, BaFin-regulated banks, Siemens, BMW).

**Differentiators vs. typical RAG portfolios:**
- German-language handling (compound words, ligatures, soft hyphens, legal § references)
- EU-sovereign tooling (Qdrant 🇩🇪, Langfuse 🇩🇪, Mistral 🇫🇷, multilingual-e5)
- Compliance/audit angle with citation-grounded retrieval
- Full evaluation rigor (golden dataset, Ragas, LLM-as-judge) — Week 2
- Deploys onto a production-grade AWS EKS MLOps platform — Week 3

---

## Project Status

| Day | Phase | Status |
|-----|-------|--------|
| 1 | Project scaffold + corpus acquisition | ✅ Done |
| 2 | PDF extraction + German legal structure detection | ✅ Done |
| 3 | Hierarchical parent-child chunking | ✅ Done |
| 4 | Embeddings + Qdrant vector store | ✅ Done |
| 5 | Hybrid search (dense + BM25) + reranker | ⬜ Planned |
| 6 | Generation (Mistral API) + Streamlit UI | ⬜ Planned |
| 7 | Mini-evaluation + golden dataset | ⬜ Planned |
| 8–14 | Week 2: Agents (LangGraph) + full evaluation | ⬜ Planned |
| 15–21 | Week 3: Production hardening + EKS deployment | ⬜ Planned |

---

## Architecture Overview

> 🔄 Diagram in progress — will be added on Day 6 when the full pipeline
> (ingestion → chunking → embeddings → retrieval → generation → UI) is complete.

---

## Day-by-Day Progress

### Day 1 — Project Scaffold + Corpus Acquisition
**Commit:** `feat(day1): project scaffold + corpus downloader for BaFin/DSGVO`

- Initialized Python 3.12 project with `uv` package manager
- Structured logging with `structlog` (JSON-ready, Loki-compatible)
- Typed config management with `pydantic-settings`
- Polite web scraper with multi-source fallback strategy:
  - DSGVO consolidated text (Berlin Data Protection Authority, 2025 edition)
  - BDSG English translation (gesetze-im-internet.de)
- Best-effort BaFin Rundschreiben scraper (documented in L-001)

**Key decisions:**
- `uv` over pip/poetry — 10-100x faster, single tool, Rust-based
- `httpx` over `requests` — async-ready, modern
- Multi-source + fallback pattern — production scrapers always have fallback layers

---

### Day 2 — PDF Extraction + German Legal Structure Detection
**Commit:** `feat(day2): PDF extraction pipeline — 86 BDSG sections, 20 DSGVO sections, 0 warnings`

- Pydantic data models as pipeline contract: `Document`, `Section`
- German-aware text cleaning pipeline (pure functions, composable):
  - Unicode NFC normalization
  - Ligature replacement (ﬁ → fi, ﬂ → fl, etc.)
  - Soft-hyphen stripping (`\xad`)
  - Line-break hyphenation repair (`Daten-\nschutz` → `Datenschutz`)
  - Page artifact removal (page numbers, running headers)
- Structural section detection across three heading formats:
  - `# Artikel N` — DSGVO Markdown-headed articles
  - `# § N` — BDSG German paragraph-sign headings
  - `**Section N**` — BDSG English bold headings (Word-generated PDFs)
- Language auto-detection (`langdetect`) with deterministic seeding
- Document-type classification (Regulation vs. BaFin Circular)
- Best-effort extraction: warnings captured per-doc, pipeline never crashes

**Results:**
| Document | Pages | Chars | Sections | Language | Warnings |
|---|---|---|---|---|---|
| `dsgvo_official_de` | 270 | 428,075 | 20 | de | 0 |
| `bdsg_official_en` | 43 | 152,684 | 86 | en | 0 |

**Key decisions:**
- `pymupdf4llm` over `pypdf`/`unstructured` — structure-preserving Markdown output
- Sections as first-class objects — enables citation-grounded retrieval
- Pure-function text cleaning — composable, unit-testable
- Best-effort with warnings — production pipelines don't crash on bad input
- Debug-by-data — inspected actual extracted markdown before writing regex

---

### Day 3 — Hierarchical Parent-Child Chunking
**Commit:** `feat(day3): hierarchical parent-child chunking — 836 children, max 256 tokens, zero warnings`

- **Parent-child hierarchy:** Small children (~200 tokens) for precise retrieval;
  large parents (full sections, ~627 tokens avg) for rich LLM generation context
- **Context-prefix injection:** Every child prefixed with `[DocTitle · SectionHeading]`
  before embedding — free retrieval recall improvement
- **Token-counted limits:** Measured on final `prefix + body` string using the
  actual `multilingual-e5-large-instruct` tokenizer — not estimated from buffer math
- **German-aware sentence splitting:** `sentence-splitter` library handles `z.B.`,
  `Art.`, `Abs.`, `Nr.` abbreviations correctly
- **Hard-split safety net:** Marathon DSGVO recital sentences (300+ tokens)
  split on token IDs and decoded back to text
- **Tiny-section merging:** Sections < 30 tokens merged into neighbors
- **Deterministic chunk IDs:** MD5 hash of `doc_id + section_id` — re-chunking
  is idempotent, no duplicate vector store entries
- **JSON Lines output:** Streaming-friendly, append-friendly, standard for ML pipelines

**Results:**
| Metric | Value |
|---|---|
| Parent chunks | 164 |
| Child chunks | 836 |
| Avg child tokens | 169 |
| Max child tokens | 256 (at hard cap — zero overflow) |
| Avg parent tokens | 627 |
| Children per parent | 5.1 |
| Documents fallen back to text | 0 |
| Warnings | 0 |

**Key decisions:**
- See [ADR-001](docs/adr/001-hierarchical-parent-child-chunking.md)
- Tokenization is not additive across string boundaries — measure-then-decide
  is the only reliable approach (see [L-004](docs/lessons-learned.md))

---

## Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.12 | Modern, type-hint-friendly |
| Package manager | `uv` | 10-100x faster than pip, Rust-based |
| PDF extraction | `pymupdf4llm` | Structure-preserving Markdown output |
| Data validation | `pydantic` v2 | Type-safe models, JSON serialization |
| Config management | `pydantic-settings` | Env-var driven, 12-factor ready |
| Logging | `structlog` | JSON logs, Loki-compatible |
| Sentence splitting | `sentence-splitter` | German-aware abbreviation handling |
| Tokenization | `transformers` (HuggingFace) | Same tokenizer at chunk + embed time |
| Vector DB | Qdrant (🇩🇪) | German-founded, hybrid search native |
| Embeddings | `multilingual-e5-large-instruct` | Best open multilingual, German-strong |
| LLM | Mistral Large (🇫🇷) | EU-sovereign, strong multilingual |
| Observability | Langfuse (🇩🇪) | LLM tracing, German-founded |
| Deployment | AWS EKS + ArgoCD | Production MLOps platform (separate repo) |

---

## Repository Structure

> 🔄 Structure stabilises on Day 6. Full directory tree will be documented then.
> Current layout: `src/` (ingestion, chunking), `scripts/`, `notebooks/`, `docs/`, `data/`.

---

## Documentation

### Architecture Decision Records
Six lightweight ADRs planned for this project. See [`docs/adr/`](docs/adr/).

| # | Decision | Status |
|---|---|---|
| 001 | Hierarchical parent-child chunking | ✅ Accepted |
| 002 | Embedding model selection | 🔄 Day 4 |
| 003 | Hybrid search strategy | 🔄 Day 5 |
| 004 | LangGraph for agent orchestration | 🔄 Day 8 |
| 005 | Evaluation methodology | 🔄 Day 9 |
| 006 | Qdrant deployment on EKS | 🔄 Day 18 |

### Lessons Learned
Four debugging postmortems documented so far. See [`docs/lessons-learned.md`](docs/lessons-learned.md).

| # | Issue | Takeaway |
|---|---|---|
| L-001 | BaFin JS-rendered scraper returned 0 PDFs | Good-enough corpus beats perfect corpus you can't get |
| L-002 | `onnxruntime` Apple Silicon wheel conflict | Platform override + `--no-deps` pattern for ML dependencies |
| L-003 | BDSG 0 sections — wrong heading format assumed | Look at the data before writing the regex |
| L-004 | Token overflow despite hard-cap enforcement | Tokenization is not additive across string boundaries |

---

## Running the Pipeline

### Prerequisites
- Python 3.12+
- `uv` package manager
- Docker Desktop (for Qdrant, Day 4+)

### Setup

```bash
git clone https://github.com/srinivas-singireddy/munich-rag-compliance
cd munich-rag-compliance
uv sync
uv pip install pymupdf4llm --no-deps
uv pip install tabulate
```

### Run the pipeline

```bash
# Step 1: Download corpus
uv run python -m scripts.download_corpus

# Step 2: Extract + structure
uv run python -m scripts.extract_corpus

# Step 3: Chunk
uv run python -m scripts.chunk_corpus

# Step 4: Embed + index (Day 4)
# uv run python -m scripts.embed_and_index

# Step 5: Launch UI (Day 6)
# uv run streamlit run src/api/app.py
```

---

## Related Projects

**MLOps Platform (AWS EKS):**
[`github.com/srinivas-singireddy/mlops-platform-aws-eks`](https://github.com/srinivas-singireddy/mlops-platform-aws-eks)

Production-grade EKS platform that hosts this RAG system as a tenant workload
in Week 3. Features: Terraform IaC, ArgoCD GitOps, ESO secrets management,
Prometheus/Grafana/Loki observability, cert-manager, ALB Controller, Karpenter.
Daily destroy/apply ritual keeps AWS spend at ~€20/month.

---

## Author

**Srinivas Singireddy** — Cloud & DevOps Solutions Architect  
Munich, Germany · [GitHub](https://github.com/srinivas-singireddy) · CKA certified

