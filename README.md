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
| 5 | Hybrid search (dense + BM25) + reranker | ✅ Done |
| 6 | Generation (Mistral API) + Streamlit UI | ✅ Done |
| 7 | Mini-evaluation + golden dataset expansion | ✅ Done |
| 8 | Agent orchestration — LangGraph (classifier, router, validator) | ✅ Done |
| 9–10 | Agent orchestration — parallel multi-article retrieval | 🔄 Next |
| 11–13 | Full evaluation harness + RAG metrics | ⬜ Planned |
| 14–16 | MapReduce pattern — aggregate analysis | ⬜ Planned |
| 17–21 | Production hardening + EKS deployment | ⬜ Planned |

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

### Day 4 — Embeddings + Qdrant Hybrid Index
**Commit:** `feat(day4): embeddings + Qdrant hybrid index — 642 children, reconciled`

- Dense embeddings via `multilingual-e5-large-instruct` (1024d) — same tokenizer as Day 3
- Sparse BM25 vectors via `fastembed` for exact-term retrieval
- Qdrant collection with named dense+sparse vectors, IDF modifier, payload indexes
- E5 prefix discipline at the encoder API boundary
- **Reconciliation discipline:** input count = indexed count, asserted at end of pipeline
- **Three-layer defense against silent data loss:** upstream disambiguation in `structure.py`,
  natural parent IDs in `chunker.py`, count reconciliation in `embed_and_index.py`
- Tolerant `ARTICLE_PATTERN` accommodates PDF extraction drift across pipeline changes
- **Result:** 178 sections detected (vs. 106 before bug-fix), 642 well-aligned children, zero loss

**Key decisions:**
- See [ADR-002](docs/adr/002-embedding-model-and-vector-store.md)
- Counterintuitive lesson: fewer chunks (642 vs 836) with better semantic alignment beats more chunks with arbitrary text windows
- See [L-005](docs/lessons-learned.md): three-layer silent data loss debugging story

---

### Day 5 — Hybrid Search (Dense + BM25) + Reranker
**Commit:** `feat(day5): hybrid search + cross-encoder reranker — P@1=79% MRR=0.871`

- Hybrid retrieval combining dense vectors (multilingual-e5) + sparse BM25 (fastembed BM42)
- Cross-encoder reranker (`BAAI/bge-reranker-v2-m3`) re-scores top-40 candidates to top-10
- Retrieval evaluation framework: Precision@1, Recall@5, MRR on 14-question golden set
- Parent promotion: child chunk retrieved, parent context returned to LLM
- Three retrieval strategies benchmarked: dense, hybrid, hybrid+rerank

**Results (14 questions):**
| Strategy | P@1 | R@5 | MRR |
|---|---|---|---|
| Dense | 79% | 100% | 0.871 |
| Hybrid | 71% | 93% | 0.780 |
| Hybrid + Rerank | 79% | 100% | 0.871 |

**Key decisions:**
- See [ADR-003](docs/adr/003-hybrid-search-and-reranker.md)
- Reranker requires `text_for_embedding` (prefix-injected) not raw text — consistent
  text representation across all pipeline stages (see [L-007](docs/lessons-learned.md))
- BM42 sparse model outperforms classic BM25 on legal German compound terms

---

### Day 6 — Generation (Mistral API) + Streamlit UI
**Commit:** `feat(day6): generation pipeline + Streamlit UI — streaming, citations, dark theme`

- `ComplianceGenerator` class: system prompt engineering for citation-grounded answers
- Streaming generation via `mistralai==1.2.5` (pinned — v2.x broke import API)
- Streamlit UI: dark theme, strategy selector (dense/hybrid/hybrid+rerank),
  expandable source citations panel, conversation history
- Prompt engineering: role framing as EU compliance expert, structured citation format,
  German/English bilingual instruction handling
- End-to-end pipeline wired: query → retrieve → rerank → generate → stream to UI

**Key decisions:**
- See [ADR-004](docs/adr/004-prompt-engineering-generation.md)
- Pin `mistralai==1.2.5` — v2.x changed the import structure mid-project
  (see [L-008](docs/lessons-learned.md))
- Streaming over batch generation — compliance users expect near-instant first token
- Citation grounding in prompt, not post-hoc — hallucination prevention by design

---

### Day 7 — Mini-Evaluation + Golden Dataset Expansion
**Commit:** `day7: expand golden set 14→25, establish eval methodology, ADR-005`

- Diagnosed both Day 5 open failures with real retrieval data — both confirmed as
  reranker-dependent passes, not corpus problems
- Discovered DSGVO/BDSG section_id namespace collision: both corpora use `art_1`
  through `art_86` — 8 golden questions required `expected_doc_id` scoping
- Expanded golden set from 14 → 25 questions across 6 failure categories:
  synonym stress, cross-lingual, specific clause, adjacent article, BDSG-specific, scope
- Introduced `eval_mode` field: `strict` (single article) vs `cluster_any`
  (broad queries with multiple valid answers)
- Fixed Q5 wording: controller obligation framing pulled art_13 instead of art_15 —
  rewrote to data subject access framing
- Added art_49 (transfer derogations) to Q11 expected set — legitimate retrieval answer

**Results (25 questions, Day 7 official baseline):**
| Strategy | P@1 | R@5 | MRR |
|---|---|---|---|
| Dense | 72% | 84% | 0.768 |
| Hybrid | 64% | 84% | 0.723 |
| Hybrid + Rerank | **80%** | **88%** | **0.828** |

Hybrid+rerank leads dense by +8% P@1 — reranker value statistically unambiguous at 25 questions.

**Open failures carried to Days 11–13:**
- Q18/Q19: English queries retrieve BDSG articles instead of DSGVO (R@5=✗) — HyDE candidate
- Q23: German query cannot bridge to English BDSG corpus — known cross-lingual limitation

**Key decisions:**
- See [ADR-005](docs/adr/005-evaluation-methodology.md)
- Retrieval eval and generation eval are intentionally separate scripts with separate metrics
- Primary metric is hybrid+rerank P@1 — dense P@1 reported for diagnostics only
- `uv run python` always — bare `python` silently picks up system interpreter
  (see [L-009](docs/lessons-learned.md))

---

### Day 8 — Agent Orchestration (LangGraph)
**Commit:** `day8: LangGraph agent — classifier, router, citation validator, ComplianceResponse`

- `src/agent/models.py` — `AgentState` TypedDict (LangGraph shared state) +
  `ComplianceResponse` Pydantic model (typed output for all callers)
- `src/agent/nodes.py` — five nodes: `query_classifier`, `retriever`,
  `context_assembler`, `generator`, `citation_validator`
- `src/agent/graph.py` — LangGraph `StateGraph`, conditional routing, `run_agent()` public API
- `docs/adr/006-langgraph-agent-orchestration.md` — ADR accepted before implementation

**Query routing:**
| Class | Behaviour |
|---|---|
| `simple_rag` | Classify → Retrieve → Assemble → Generate → Validate citations |
| `multi_article` | Same path — parallel retrieval scaffolded, completed Days 9–10 |
| `out_of_scope` | Short-circuit at classifier — no retrieval, no generation, no LLM cost |

**Smoke test results:**
| Query | Type | Citations | Confidence |
|---|---|---|---|
| "What does Art. 5 DSGVO say?" | simple_rag | [] | 0.248 |
| "Compare Art. 28 and Art. 29 DSGVO" | multi_article | [art_28, art_29] | 0.334 |
| "What is the weather in Munich?" | out_of_scope | [] | 0.0 |

**Citation validator in action:**
- Mistral hallucinated `art_32` and `art_15` in the multi_article response
- `citation_validator` caught and stripped both — neither was in retrieved context
- Final citations contain only articles verifiably present in retrieved chunks

**Key decisions:**
- See [ADR-006](docs/adr/006-langgraph-agent-orchestration.md)
- `AgentState` is a `TypedDict` not Pydantic — LangGraph requires partial state updates
  per node; Pydantic requires all fields at construction
- Classifier uses Mistral (same model, consistent stack) — lighter model post-EKS
  as planned enhancement once production latency is measured
- Only 2 of 5 nodes invoke an LLM — classifier and generator; remaining 3 are
  deterministic Python (retriever, context_assembler, citation_validator)
- See [L-010](docs/lessons-learned.md), [L-011](docs/lessons-learned.md),
  [L-012](docs/lessons-learned.md)

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
| Agent orchestration | LangGraph 0.3.34 | Stateful graph, conditional routing, auditable |
| Observability | Langfuse (🇩🇪) | LLM tracing, German-founded |
| Deployment | AWS EKS + ArgoCD | Production MLOps platform (separate repo) |

---

## Repository Structure

> 🔄 Structure stabilises on Day 6. Full directory tree will be documented then.
> Current layout: `src/` (ingestion, chunking, embeddings, retrieval, generation, agent),
> `scripts/`, `notebooks/`, `docs/`, `data/`.

---

## Documentation

### Architecture Decision Records
See [`docs/adr/`](docs/adr/).

| # | Decision | Status |
|---|---|---|
| 001 | Hierarchical parent-child chunking | ✅ Accepted |
| 002 | Embedding model and vector store selection | ✅ Accepted |
| 003 | Hybrid search strategy and reranker selection | ✅ Accepted |
| 004 | Prompt engineering and generation architecture | ✅ Accepted |
| 005 | Evaluation methodology | ✅ Accepted |
| 006 | LangGraph agent orchestration | ✅ Accepted |
| 007 | Qdrant deployment on EKS | 🔄 Day 18 |

### Lessons Learned
Twelve debugging postmortems documented so far. See [`docs/lessons-learned.md`](docs/lessons-learned.md).

| # | Issue | Takeaway |
|---|---|---|
| L-001 | BaFin JS-rendered scraper returned 0 PDFs | Good-enough corpus beats perfect corpus you can't get |
| L-002 | `onnxruntime` Apple Silicon wheel conflict | Platform override + `--no-deps` pattern for ML dependencies |
| L-003 | BDSG 0 sections — wrong heading format assumed | Look at the data before writing the regex |
| L-004 | Token overflow despite hard-cap enforcement | Tokenization is not additive across string boundaries |
| L-005 | Silent data loss — three-layer root cause | Reconcile input/output counts; no hash truncation; tolerant patterns |
| L-006 | PDF margin annotations mistaken for headings | Inspect source PDF layout; Python indentation is silently load-bearing |
| L-007 | Reranker required text_for_embedding not text_raw | Multi-stage pipelines need consistent text representations |
| L-008 | mistralai v2.x broke `from mistralai import Mistral` | Pin exact major.minor for fast-moving AI SDKs |
| L-009 | `[tool.uv.env]` not supported in uv 0.11.7 | Use `.env` file for PYTHONPATH; always use `uv run python` not bare `python` |
| L-010 | `RetrievalResult` fields differ from Qdrant `ScoredPoint` — assumed `.id` and `.payload` | Always grep the actual return type before wrapping existing functions |
| L-011 | Parent `chunk_id` is top-level in `chunks_parents.jsonl`, not nested under `metadata` | Run `head -1` on jsonl files and print key structure before writing lookup logic |
| L-012 | `uv run pip` is not venv-aware — reports against system pip, not project venv | Always use `importlib.metadata.version()` for package introspection |

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
PYTHONPATH=. uv run python -m scripts.download_corpus

# Step 2: Extract + structure
PYTHONPATH=. uv run python -m scripts.extract_corpus

# Step 3: Chunk
PYTHONPATH=. uv run python -m scripts.chunk_corpus

# Step 4: Embed + index
PYTHONPATH=. uv run python -m scripts.embed_and_index

# Step 5: Run retrieval evaluation
PYTHONPATH=. uv run python -m scripts.eval_retrieval

# Step 6: Launch UI
PYTHONPATH=. uv run streamlit run app/streamlit_app.py

# Step 7: Run compliance agent (Day 8+)
PYTHONPATH=. uv run python -c '
from src.agent.graph import run_agent
response = run_agent("What are the lawful bases for processing under DSGVO?")
print(response.answer)
'
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