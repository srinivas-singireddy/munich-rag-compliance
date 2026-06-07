# Munich RAG Compliance System

**A production-grade compliance AI for German regulated enterprises** — built on EU-sovereign infrastructure, deployed as a tenant workload on a production AWS EKS platform.

> Hybrid retrieval · LangGraph agent orchestration · DSGVO/BDSG · BaFin-ready · Full evaluation rigor

**Target:** Allianz, Munich Re, BaFin-regulated banks, and any enterprise operating under GDPR/DSGVO where compliance answers must be cited, auditable, and EU-data-resident.

---

## What this system does

| Capability | Detail |
|---|---|
| **Regulatory Q&A** | Ask questions in German or English across DSGVO + BDSG corpus |
| **Citation-grounded answers** | Every answer cites the specific article — no hallucinated law |
| **Agent routing** | LangGraph classifier routes simple, multi-article, and out-of-scope queries |
| **Parallel multi-article retrieval** | Concurrent retrieval across multiple legal references (3× faster) |
| **PII detection** | Presidio-based scrubbing before any data leaves the system |
| **Full evaluation harness** | 25-question golden dataset, Ragas metrics, LLM-as-judge scoring |
| **EU-sovereign stack** | Qdrant 🇩🇪, Langfuse 🇩🇪, Mistral 🇫🇷 — no US cloud AI providers |
| **EKS deployment** | Runs as tenant workload on production MLOps platform (Week 3) |

---

## Why this is different from typical RAG portfolios

Most RAG demos use English Wikipedia, OpenAI embeddings, and Pinecone. This system is built for a specific, hard problem:

**German legal text is genuinely difficult.**
- Compound words (`Datenschutzbeauftragter`, `Aufsichtsbehörde`) break naive tokenizers
- Ligatures (`ﬁ`, `ﬂ`), soft hyphens, and line-break hyphenation corrupt extracted text
- Legal `§` references and `Art. N` cross-references require structure-aware parsing
- Cross-lingual queries (English question → German DSGVO article) need multilingual embeddings

**Compliance use cases have zero tolerance for hallucination.**
- Citation grounding is enforced at prompt-engineering time, not post-hoc
- Every retrieval result traces back to a specific article and document
- Evaluation is systematic: golden dataset, Precision@1, Recall@5, MRR, LLM-as-judge

**EU data sovereignty is a hard requirement for regulated enterprises.**
- Qdrant (Germany), Langfuse (Germany), Mistral (France) — all European
- Local Llama 3.3 fallback for air-gapped or cost-sensitive scenarios
- Architecture is BaFin BAIT/DORA-aligned by design

---

## Architecture

```
User Query
    │
    ▼
┌─────────────────────────────────────────────────────┐
│              LangGraph Agent                        │
│                                                     │
│  ┌─────────────┐    ┌──────────────────────────┐   │
│  │  Classifier  │───▶│  Router                  │   │
│  │  (Mistral)   │    │  simple_rag /             │   │
│  └─────────────┘    │  multi_article /           │   │
│                     │  out_of_scope              │   │
│                     └──────────┬─────────────────┘   │
│                                │                     │
│              ┌─────────────────┼──────────────────┐  │
│              ▼                 ▼                  ▼  │
│       Single retrieve   Parallel retrieve    Short   │
│       (hybrid + BM25)   (ThreadPoolExecutor) circuit │
│              │                 │                     │
│              └────────┬────────┘                     │
│                       ▼                              │
│              ┌─────────────────┐                     │
│              │ Context assembly │                    │
│              │ (parent chunks)  │                    │
│              └────────┬────────┘                     │
│                       ▼                              │
│              ┌─────────────────┐                     │
│              │   Generator      │                    │
│              │   (Mistral API)  │                    │
│              └────────┬────────┘                     │
│                       ▼                              │
│              ┌─────────────────┐                     │
│              │ Citation         │                    │
│              │ validator        │                    │
│              └────────┬────────┘                     │
└───────────────────────┼─────────────────────────────┘
                        ▼
               ComplianceResponse
               (answer + citations + confidence)
```

**Retrieval pipeline:**
```
PDF corpus → pymupdf4llm extraction → German-aware cleaning
    → Hierarchical chunking (parent/child, 256 token max)
    → multilingual-e5-large-instruct embeddings (1024d)
    → Qdrant hybrid index (dense + BM25 sparse)
    → Cross-encoder reranker (BAAI/bge-reranker-v2-m3)
    → Parent promotion → LLM context window
```

**Retrieval performance (25-question golden dataset):**

| Strategy | P@1 | R@5 | MRR |
|---|---|---|---|
| Dense only | 72% | 84% | 0.768 |
| Hybrid (dense + BM25) | 64% | 84% | 0.723 |
| Hybrid + rerank ✅ | **80%** | **88%** | **0.828** |

---

## Build status

| Phase | Capability | Status |
|---|---|---|
| **Ingestion** | PDF extraction, German legal structure detection | ✅ Complete |
| **Chunking** | Hierarchical parent-child, German-aware sentence splitting | ✅ Complete |
| **Retrieval** | Hybrid search (dense + BM25), cross-encoder reranker | ✅ Complete |
| **Generation** | Mistral API, citation-grounded prompt engineering, Streamlit UI | ✅ Complete |
| **Evaluation** | 25-question golden dataset, Ragas metrics, LLM-as-judge | ✅ Complete |
| **Agent** | LangGraph orchestration — classifier, router, validator | ✅ Complete |
| **Parallel retrieval** | Multi-article concurrent retrieval, 3× latency improvement | ✅ Complete |
| **Full eval harness** | MapReduce aggregate analysis, extended Ragas suite | ⬜ Week 2 |
| **EKS deployment** | Tenant workload on MLOps platform, Qdrant on Kubernetes | ⬜ Week 3 |

---

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.12 + `uv` | Modern, type-safe, 10-100× faster package management |
| PDF extraction | `pymupdf4llm` | Structure-preserving Markdown — critical for legal text |
| Data validation | Pydantic v2 | Typed pipeline contracts, JSON serialization |
| Logging | `structlog` | JSON logs, Loki-compatible for EKS observability |
| Vector DB | Qdrant 🇩🇪 | German-founded, native hybrid search, EU-resident |
| Embeddings | `multilingual-e5-large-instruct` | Best open multilingual model, strong on German |
| Reranker | `BAAI/bge-reranker-v2-m3` | Cross-encoder reranking, +8% P@1 over dense alone |
| LLM | Mistral Large 🇫🇷 | EU-sovereign, strong multilingual, DSGVO-compliant |
| Local fallback | Llama 3.3 | Air-gapped / cost-sensitive scenarios |
| Agent | LangGraph 0.3.34 | Stateful graph, conditional routing, auditable traces |
| Observability | Langfuse 🇩🇪 | LLM tracing, German-founded, EU-resident |
| PII detection | Presidio | Microsoft open-source, production-grade |
| Evaluation | Ragas + LLM-as-judge | Dual-track: retrieval metrics + generation quality |
| Deployment | AWS EKS + ArgoCD | Production MLOps platform (separate repo below) |

---

## Key engineering decisions

Eight Architecture Decision Records document every major choice. Selected highlights:

**ADR-001 — Hierarchical parent-child chunking**
Small children (≤256 tokens) for precise retrieval precision; large parents (full sections, ~627 tokens avg) for rich generation context. Context-prefix injection on every child chunk (`[DocTitle · SectionHeading]`) before embedding — free recall improvement with no extra infrastructure.

**ADR-003 — Hybrid search + reranker**
BM42 sparse model outperforms classic BM25 on German legal compound terms. Reranker adds +8% P@1 at 25 questions — statistically unambiguous. Key insight: reranker requires prefix-injected text (`text_for_embedding`), not raw text — consistent representation across all pipeline stages.

**ADR-008 — Parallel multi-article retrieval**
Separation of concerns across two stages: article-scoped sub-queries (`Artikel N DSGVO`) for retrieval precision; original full query for reranking topic relevance. Conflating the two lets topic semantics override article identity in the embedding space. `ThreadPoolExecutor` over asyncio — `retrieve()` is sync/blocking; async adds complexity with no benefit until EKS async clients.

Full ADR index: [`docs/adr/`](docs/adr/)

---

## Lessons learned

Fourteen debugging postmortems documented. A selection of the non-obvious ones:

| # | Root cause | Takeaway |
|---|---|---|
| L-004 | Token overflow despite hard-cap logic | Tokenization is not additive across string boundaries — measure the final string, not the parts |
| L-005 | Silent data loss — 836 → 642 chunks | Three-layer defence: upstream disambiguation + natural IDs + count reconciliation at pipeline end |
| L-013 | Race condition under concurrent threads | Warm all `@lru_cache` models at module import time before spawning ThreadPoolExecutor |
| L-014 | Wrong chunks returned in multi-article queries | Article-scoped retrieval + topic-scoped reranking — never conflate the two stages |

Full postmortem log: [`docs/lessons-learned.md`](docs/lessons-learned.md)

---

## Running the system

### Prerequisites
- Python 3.12+, `uv` package manager, Docker Desktop (Qdrant)

### Setup

```bash
git clone https://github.com/srinivas-singireddy/munich-rag-compliance
cd munich-rag-compliance
uv sync
uv pip install pymupdf4llm --no-deps
```

### Run the full pipeline

```bash
# 1. Download corpus (DSGVO + BDSG)
PYTHONPATH=. uv run python -m scripts.download_corpus

# 2. Extract and structure
PYTHONPATH=. uv run python -m scripts.extract_corpus

# 3. Chunk (hierarchical parent-child)
PYTHONPATH=. uv run python -m scripts.chunk_corpus

# 4. Embed and index into Qdrant
PYTHONPATH=. uv run python -m scripts.embed_and_index

# 5. Run retrieval evaluation (25-question golden dataset)
PYTHONPATH=. uv run python -m scripts.eval_retrieval

# 6. Launch Streamlit UI
PYTHONPATH=. uv run streamlit run app/streamlit_app.py

# 7. Run compliance agent directly
PYTHONPATH=. uv run python -c '
from src.agent.graph import run_agent
response = run_agent("What are the lawful bases for processing under DSGVO?")
print(response.answer)
'
```

---

## Related: MLOps platform

This system deploys as a **tenant workload** on a production-grade AWS EKS platform.

[`github.com/srinivas-singireddy/mlops-platform-aws-eks`](https://github.com/srinivas-singireddy/mlops-platform-aws-eks)

Platform features: Terraform IaC (3 roots: network/cluster/platform), ArgoCD GitOps, External Secrets Operator, kube-prometheus-stack, Loki, Grafana Alloy, cert-manager, ALB Controller, Karpenter 1.8.6, AL2023 nodes. Daily destroy/apply ritual keeps AWS spend at ~€20/month.

**The story these two repos tell together:** platform engineering (the substrate) + AI engineering (the tenant workload) demonstrated end-to-end. This is how mature platform organisations think about deploying AI.

---

## Author

**Srinivas Singireddy** — Senior Solutions Architect  
25+ years enterprise architecture · Financial Services & Insurance · Munich, Germany  
CKA certified (Linux Foundation, valid through 2027) · German PR · EU work auth  

[GitHub](https://github.com/srinivas-singireddy) · [LinkedIn](https://linkedin.com/in/srinivas-singireddy)