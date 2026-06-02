# ADR-006: Agent Orchestration with LangGraph

**Status:** Accepted  
**Date:** 2025-06-01  
**Deciders:** Srinivas Singireddy  
**Project:** munich-rag-compliance

---

## Context

Days 1–7 produced a working RAG pipeline:

```
User Query → Hybrid Search → Reranker → Mistral → Response (string)
```

This pipeline is linear and stateless. Every query — regardless of type, scope,
or complexity — follows the identical execution path. Three problems emerge at
production scale:

**1. No query routing.**
A simple single-article lookup ("What does Art. 5 say?") and a complex
cross-corpus comparison ("Compare Art. 28 DSGVO vs Art. 29 DSGVO") consume
identical compute. The pipeline has no mechanism to choose a strategy based
on query intent.

**2. No out-of-scope gate.**
Queries unrelated to DSGVO or BDSG (e.g. "What is the VAT rate in Germany?")
are processed through the full pipeline. The model retrieves the least-irrelevant
chunks and generates a plausible-sounding but fabricated compliance answer.
For a BaFin-regulated client, this is an unacceptable failure mode.

**3. Unverified citations.**
The generator produces article citations, but nothing verifies that cited
article IDs were actually present in the retrieved context. Mistral can and
does hallucinate citation references.

**4. Untyped output.**
The pipeline returns a raw string. Downstream consumers — audit logs, APIs,
BaFin reporting interfaces — require structured, typed data.

---

## Decision

Wrap the existing RAG pipeline in a **LangGraph state machine** with five
declared nodes. The agent classifies query intent first, routes accordingly,
and returns a typed `ComplianceResponse` Pydantic object.

### Agent Location

```
src/agent/
├── __init__.py
├── models.py          ← ComplianceResponse, AgentState
├── nodes.py           ← all five node functions
└── graph.py           ← LangGraph StateGraph wiring
```

### Query Classification

Three classes, constrained LLM output:

| Class | Description | Routing |
|---|---|---|
| `simple_rag` | Single article lookup, definition, or explanation | Standard retrieve → generate |
| `multi_article` | Comparison, cross-reference, or aggregation across articles | Parallel retrieval per cluster → merge → generate |
| `out_of_scope` | Unrelated to DSGVO, BDSG, or data protection law | Short-circuit — no retrieval, no generation |

Classification is performed by a targeted Mistral call with a constrained
system prompt. Output is validated as one of the three classes before routing.

### Five Nodes

```
query_classifier  → LLM call (Mistral, constrained 3-class output)
retriever         → existing hybrid_search.retrieve() — no LLM
context_assembler → deduplicate + rank parent chunks — no LLM
generator         → existing ComplianceGenerator — LLM call (Mistral)
citation_validator→ set membership check — no LLM
```

Only 2 of 5 nodes invoke an LLM. The remaining 3 are deterministic Python.

### Graph Topology

```
START
  ↓
query_classifier
  ↓
  ├── out_of_scope  → END (short-circuit, no retrieval)
  ├── simple_rag    → retriever → context_assembler → generator → citation_validator → END
  └── multi_article → retriever → context_assembler → generator → citation_validator → END
```

Multi-article parallel retrieval is scaffolded in Day 8 and completed in
Days 9–10.

### Output Schema

```python
class ComplianceResponse(BaseModel):
    answer: str
    citations: list[str]          # article IDs present in retrieved context
    confidence: float             # 0.0–1.0, derived from reranker scores
    query_type: QueryType         # simple_rag | multi_article | out_of_scope
    retrieved_sections: list[str] # section_ids of all retrieved chunks
    out_of_scope: bool            # True if query was rejected at classifier
```

### Model Choice — Classifier

Mistral (same model as generator, `mistralai==1.2.5` pinned).

**Rationale:** Stack consistency over marginal cost savings at this stage.
Classifier prompt is constrained to 3-class output — cheap per call regardless
of model. Post-EKS deployment, replace with a lighter model once latency and
cost are measured in production. This is logged as a planned enhancement.

---

## Alternatives Considered

### Alternative 1 — Keyword-based routing (no LLM classifier)

Route based on presence of keywords: "Art.", "DSGVO", "BDSG", "Datenschutz".

**Rejected:** Brittle. Does not handle paraphrased queries, mixed-language
inputs, or adversarial phrasing. Fails silently — no logging of routing
decisions.

### Alternative 2 — Embedding similarity gate

Embed the query and compute cosine distance to a corpus centroid. Reject
if distance exceeds threshold.

**Rejected:** Requires threshold calibration. "Munich tax law" may pass.
Threshold drifts as corpus evolves. Harder to debug routing failures.

### Alternative 3 — Plain Python if/else orchestration

Wire the pipeline with conditional Python logic, no framework.

**Rejected:** Achieves Day 8 goals but does not compose for Days 14–16
(MapReduce extension). LangGraph's graph declaration makes the MapReduce
dispatcher a natural extension — new nodes added, existing nodes unchanged.
The graph is also the architecture diagram — directly explainable to clients.

### Alternative 4 — LangChain LCEL chains

Use LangChain Expression Language instead of LangGraph.

**Rejected:** LCEL is optimised for linear chains. Conditional branching,
parallel retrieval, and state management across nodes are more natural in
LangGraph's explicit StateGraph model.

---

## Consequences

**Positive:**
- Query routing eliminates wasted compute on out-of-scope queries
- Citation validation eliminates hallucinated article references
- Typed `ComplianceResponse` enables downstream API and audit log consumption
- Declared state machine is directly auditable — every decision path is logged
- Graph topology composes naturally for MapReduce extension (Days 14–16)
- LangGraph tracing integrates with LangSmith for production observability

**Negative:**
- One additional Mistral call per query (classifier). Acceptable at this stage.
- LangGraph adds a dependency. Pinned to stable release.
- Multi-article parallel retrieval is scaffolded Day 8, completed Days 9–10.
  Simple and out-of-scope paths are fully functional on Day 8.

---

## Implementation Notes

- Agent code lives entirely in `src/agent/` — existing pipeline code unchanged
- Streamlit app updated to call agent graph instead of pipeline directly
- `mistralai` remains pinned at `1.2.5` — classifier uses same client instance
- `AgentState` is a TypedDict — new Python pattern, explained in code comments
- Post-EKS enhancement: evaluate replacing Mistral classifier with a lighter
  model (e.g. open-source classifier) once production latency is measured

---

## Related Decisions

- ADR-003: Hybrid search + reranker — retriever node wraps this
- ADR-004: Prompt engineering — generator node reuses existing prompts
- ADR-005: Evaluation methodology — agent output feeds Days 11–13 eval harness
- ADR-007: Qdrant EKS deployment (previously ADR-006, renumbered)