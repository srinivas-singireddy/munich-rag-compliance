# ADR-002: Embedding Model and Vector Store Selection

**Date:** 2026-05-11
**Status:** Accepted
**Deciders:** Srinivas Singireddy

## Context

The RAG system needs to embed 642 child chunks (German + English legal text)
into searchable vectors and store them with both semantic (dense) and exact-keyword
(sparse) retrieval capability. Decision factors:
- German-language retrieval quality (primary use case)
- EU-sovereign tooling story (Munich enterprise positioning)
- Tokenizer consistency with chunking pipeline (ADR-001 dependency)
- Inference cost at scale (ideally zero per query)
- Production operability on AWS EKS (Week 3)

## Options Considered

### Embedding model

| Option | Pros | Cons |
|---|---|---|
| `multilingual-e5-large-instruct` | Strong German; instruction-tuned; open weights; matches Day-3 tokenizer | 2.2 GB model; ~150ms/chunk CPU |
| `multilingual-e5-base` | Faster (450 MB) | Slightly weaker on German technical text |
| `BAAI/bge-m3` | Multilingual, multi-function | Newer, less proven; would force tokenizer change |
| Cohere `embed-multilingual-v3.0` | Strong API; managed | Paid; EU residency requires contract negotiation |
| OpenAI `text-embedding-3-large` | Strong English | Not EU-hosted; weaker on German technical text |

### Vector store

| Option | Pros | Cons |
|---|---|---|
| Qdrant (open, German-founded) | Native named dense+sparse vectors; clean Helm chart for EKS; strong payload filtering | One more stateful service to operate |
| Weaviate | Open, hybrid search | More opinionated schema; heavier operationally |
| pgvector | Postgres extension; simple ops | Sparse-search story significantly weaker than Qdrant native BM25 |
| Pinecone | Managed service | Not EU-sovereign by default; per-vector pricing |

## Decision

**`multilingual-e5-large-instruct` + Qdrant with native hybrid (dense + BM25 sparse).**

Rationale: The tokenizer matches the one used at chunking time (ADR-001
dependency), eliminating a class of silent-truncation bugs. The model has
demonstrated strong German performance on legal text. Open weights mean zero
per-query inference cost, enabling unlimited evaluation runs in Week 2.
Qdrant's native hybrid support means we can defer Elasticsearch and run with a
single retrieval service; its German origin and EU-friendly hosting align with
the Munich compliance narrative.

The e5 family requires `passage: ` / `query: ` prefixes for documents and
queries respectively — these are applied at the encoder API boundary
(`src/embeddings/encoder.py`) so callers cannot accidentally embed without them.

## Consequences

**Positive**
- Tokenizer parity with chunking guarantees no silent truncation at embed time.
- Native named-vector hybrid search ready for ADR-003 (Day 5 reranker layer).
- Zero per-query embedding cost — full evaluation can run at any scale without API budget.
- Qdrant payload filters enable retrieval scoped by language or doc_type at no cost.
- EU-sovereign stack supports the audit/sovereignty story for Munich enterprises.
- One Docker image to deploy in Week 3 EKS; the official Qdrant Helm chart maps cleanly.

**Negative**
- 2.2 GB model first-time download; CPU inference is ~150ms/chunk (acceptable for our corpus, slow for very large scale).
- One more stateful service in the EKS deployment (Qdrant with PVC) — see Week 3 plan for snapshot-to-S3 backup design.
- Sparse BM25 via fastembed is good but less tunable than Elasticsearch (no field-level boosting).

**Revisit when:**
- Corpus exceeds ~100k chunks (Qdrant still fine, but consider GPU embedding for ingestion speed).
- Day 7 evaluation shows German recall < 0.75 (would prompt model reconsideration — `bge-m3` would be the alternative).
- A managed-API embedder offers both EU residency and a clear cost advantage at our throughput.

## References

- `src/embeddings/encoder.py`
- `src/retrieval/vector_store.py`
- e5 paper: https://arxiv.org/abs/2402.05672
- Qdrant docs: https://qdrant.tech/documentation/
- ADR-001 (chunking — tokenizer dependency)
