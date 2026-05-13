# ADR-003: Hybrid Search Strategy and Reranker Selection

**Date:** 2026-05-13
**Status:** Accepted
**Deciders:** Srinivas Singireddy

## Context

Day 4 retrieval showed a false positive at rank 1 for a German penalty
query — Article 10 ranked above Article 83 because the embedding model
over-generalised "Strafen" to "strafrechtlich". German legal vocabulary
has dense synonym clusters (Strafe/Geldbuße/Sanktion) that confuse
bi-encoders. Additionally, exact regulatory citations ("Artikel 83 Absatz
4") need verbatim matching that dense search cannot guarantee.

## Options Considered

**Embedding model for sparse:** BM25 (Qdrant/bm25) vs bm42
(Qdrant/bm42-all-minilm-l6-v2-attentions). BM25 diagnostic showed only
1 shared token between "Welche Strafen drohen" and "Geldbußen und
Sanktionen" — equal-weight scoring, no IDF differentiation. Switched to
bm42 which uses transformer attention weights; overlap remained at 1
because the core issue is German synonym mismatch, not tokenizer quality.

**Reranker input:** text_raw (chunk body only) vs text_for_embedding
(chunk with section-heading prefix). Initial implementation used
text_raw — reranker couldn't see "Artikel 83" in the input and penalised
correct results, dropping P@1 from 79% to 33%. Fixed to use
text_for_embedding; reranker P@1 restored to 79%.

**Fusion method:** RRF (parameter-free, rank-based) vs linear score
combination (requires normalising incomparable scales). RRF chosen.

## Decision

**Hybrid (RRF, bm42 sparse) + BAAI/bge-reranker-v2-m3, using
text_for_embedding as reranker input.**

## Evaluation Results (14-question golden set)

| Strategy | P@1 | R@5 | MRR |
|---|---|---|---|
| Dense baseline | 79% | 100% | 0.871 |
| Hybrid (dense + bm42) | 71% | 93% | 0.780 |
| Hybrid + rerank | 79% | 100% | 0.871 |

**Key finding — query-type dependency:**
- Hybrid *helps* exact citation queries ("Artikel 83 Absatz 4 Geldbußen")
  because sparse exact-match boosts the right article
- Hybrid *hurts* conceptual German queries ("Welche Strafen drohen?")
  because German synonym mismatch ("Strafen" ≠ "Geldbußen") means
  sparse adds noise, not signal
- The reranker rescues hybrid's conceptual query regressions because
  it sees query + chunk together and recognises semantic relevance
  even when sparse scores are poor

**Three persistent failures (3/14 questions):**
1. "Strafen drohen" → art_10 false positive (synonym gap, needs HyDE
   or query expansion)
2. "Rechte betroffener Personen" → art_18 (adjacent article, borderline
   acceptable)
3. "Right to be forgotten" (English) → art_35 (cross-lingual gap)

## Consequences

**Positive**
- 100% R@5 on 14-question benchmark — all answers retrievable in top 5
- Reranker at parity with dense while providing better latency tradeoff
  at scale (dense CPU inference vs reranker CPU inference both ~150ms)
- Citation queries reliably retrieve correct articles (4/4 citation
  questions correct across all strategies)
- Full observability: strategy parameter enables A/B comparison in prod

**Negative**
- Hybrid alone regresses conceptual German queries (-8 P@1 points)
- Reranker adds ~300-500ms latency per query
- 500MB additional model (bge-reranker-v2-m3)
- BM25/bm42 provides minimal value on conceptual German queries due to
  synonym richness of legal vocabulary

**Revisit when:**
- Week 2 eval introduces HyDE (hypothetical document embeddings) for
  the synonym gap — expected to lift the 3 persistent failures
- Latency profiling shows reranker is the bottleneck (then: distilled
  reranker or ONNX quantisation)
- Corpus expands to include English translations of DSGVO articles
  (would fix cross-lingual gap)

## References

- `src/retrieval/hybrid_search.py`
- `data/eval/golden.jsonl`
- `data/eval/retrieval_eval_results.json`
- Qdrant hybrid queries: https://qdrant.tech/documentation/concepts/hybrid-queries/
- BGE reranker: https://huggingface.co/BAAI/bge-reranker-v2-m3