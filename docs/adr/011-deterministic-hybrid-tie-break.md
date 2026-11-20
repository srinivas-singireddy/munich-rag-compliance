# ADR-011: Deterministic Tie-Breaking in Hybrid Search

**Date:** 2026-11-20
**Status:** Accepted
**Deciders:** Srinivas Singireddy
**Context:** Day 11 — Evaluation reproducibility (prerequisite for `make reproduce` and the CI eval gate)

---

## Context

Hybrid search fuses dense and BM42 sparse results with Reciprocal Rank Fusion,
executed server-side in Qdrant. RRF scores are simple fractions (0.5,
0.33333334, …), so exact ties are common, and Qdrant returns tied points in
arbitrary order. Observed effects (L-018):

- Q06: `art_33` and `art_83` both scored exactly 0.5 and swapped across runs.
- Plain hybrid: identical runs gave P@1 16/25 or 17/25 (MRR 0.7233–0.7433).
- With `limit=top_k`, the server cut inside ties at position 40, so the
  candidate set handed to the reranker varied (Q12).

hybrid+rerank stayed stable in every run, but only because the reranker happens
to break the ties — an observation, not a guarantee. A CI gate on
non-reproducible metrics would fail pull requests at random.

## Decision

1. Request the full fused set from Qdrant: outer `limit=2 * top_k`. Each
   prefetch returns at most `top_k`, so nothing is cut server-side.
2. Sort locally by `(-score, chunk_id)`, then cut to `top_k`.
3. Tie-break on `chunk_id`, not the Qdrant point ID. `chunk_id` is an MD5 of
   `doc_id` + `section_id` (names, not text), so it is identical across index
   rebuilds — required because the platform rebuilds the index on every deploy.

### Why this tie-break: consistent, not "right"

- When two pieces have exactly the same score, the system has no evidence to
  prefer either one. Any choice is arbitrary.
- A hash has nothing to do with meaning, so sorting by it is a coin toss that
  always lands the same way. It favours no law or article for a reason.
- The goal is the same answer on every run and every machine, so the automated
  quality check is trustworthy. Consistency is the goal, not better quality.

## Alternatives considered

- **Leave as is** — rejected: metrics not reproducible; CI gate would be flaky.
- **Tie-break on Qdrant point ID** — rejected: may change on index rebuild.
- **Tie-break on dense (meaning-search) score** — uses real evidence instead of
  an arbitrary order. Deferred as a future enhancement: more code, and its
  effect on figures needs its own evaluation.

## Consequences

- Retrieval is reproducible. Verified: Q06 identical ×4; hybrid identical ×3;
  hybrid+rerank identical ×2.
- Figures: hybrid+rerank unchanged (P@1 80%, R@5 88%, MRR 0.828). Hybrid now
  fixed at P@1 64%, R@5 84%, MRR 0.727 (README previously 0.723, one sample).
- Deterministic ≠ correct: in Q06 the tie-break ranks `art_83` above the correct
  `art_33`, so hybrid settled at 16/25, not 17/25.
- Fetching up to 80 instead of 40 points per hybrid query: negligible cost at
  this corpus size (642 children).
- Residual risk: ties inside each individual prefetch list at its `limit` cut
  are still possible (dense float ties are rare; sparse ties possible). Monitor
  via the determinism check in `make reproduce` (D6).