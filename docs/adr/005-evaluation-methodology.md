# ADR-005: Evaluation Methodology

**Date:** 2026-05-14
**Status:** Accepted
**Deciders:** Srinivas Singireddy

## Context

Days 1–6 built retrieval and generation. Day 7 requires a systematic evaluation
methodology that can scale from the initial 14-question golden set through the
full harness planned for Days 11–13.

Key constraints:
- Two corpora with overlapping section_id namespace (DSGVO + BDSG both use art_N)
- Queries span two languages (DE/EN) against two corpus languages (DE/EN)
- Some questions have single correct articles; others span a cluster
- Evaluation must remain reproducible as the golden set grows

## Decisions

### 1. Golden Set Format

Each question in `data/eval/golden.jsonl` carries:
- `question` — the query string
- `expected_section_ids` — list of acceptable section_id values
- `expected_doc_id` — optional, scopes match to a specific document
- `language` — `de` or `en`
- `expected_articles` — human-readable article numbers
- `eval_mode` — `strict` or `cluster_any`
- `notes` — diagnosis and carry-forward actions

### 2. Two Eval Modes

**strict** (default when eval_mode absent): P@1 passes only if top-1 result
matches an expected_section_id exactly. Used for single-article questions.

**cluster_any**: P@1 passes if top-1 result is any member of the expected
cluster. Used for broad queries where the corpus contains multiple equally
valid answers (e.g. "Welche Rechte haben betroffene Personen" spans art_15–21).

Rationale: applying strict P@1 to cluster queries produces false failures that
obscure real retrieval problems. Separating modes makes failures meaningful.

### 3. Doc Scoping

When `expected_doc_id` is set, a result only counts as a hit if both
`section_id` and `doc_id` match. Required because DSGVO and BDSG share
identical `art_N` section_id values across 80+ sections.

Without doc scoping, BDSG art_83 (Compensation) would count as a hit for
queries targeting DSGVO art_83 (Geldbußen). This would produce false passes
on penalty queries.

### 4. Primary Metric: Hybrid+Rerank P@1

Production strategy is hybrid+rerank. All trend comparisons use
hybrid+rerank P@1 as the primary metric. Dense and hybrid are reported
for diagnostic purposes only.

Rationale: dense P@1 can exceed hybrid+rerank P@1 on small golden sets
due to noise. At 25 questions, hybrid+rerank leads by +8% P@1 — the
reranker's value is now statistically meaningful.

### 5. Retrieval Eval vs End-to-End Eval

`scripts/eval_retrieval.py` measures retrieval quality only (context finding).
`scripts/eval_e2e.py` (Days 11–13) will measure generation quality
(answer correctness, citation accuracy, hallucination rate).

These are intentionally separate because retrieval failures and generation
failures have different root causes and different fixes.

### 6. Known Failure Categories

| Category | Example | Resolution |
|---|---|---|
| Synonym gap | Strafen vs Geldbußen | Reranker compensates — acceptable |
| Cross-lingual dense gap | EN query → DE art_28 | HyDE investigation Days 11-13 |
| BDSG/DSGVO collision | art_26 in both corpora | Doc scoping in golden set |
| German→English corpus | DE query → EN BDSG | Known limitation, Days 11-13 |
| Adjacent article | art_32 vs art_33 | Acceptable R@5 pass |

## Day 7 Baseline (25 questions, hybrid+rerank)

| Metric | Value |
|---|---|
| P@1 | 80% |
| R@5 | 88% |
| MRR | 0.828 |

Target for Days 11-13 full harness: P@1 ≥ 85%, R@5 ≥ 92%, MRR ≥ 0.870

## Consequences

- Golden set is the single source of truth for retrieval quality
- All future retrieval changes must be validated against this set before merge
- `eval_mode: cluster_any` questions must not grow beyond 20% of the set
- New questions require doc scoping whenever section_id collision is possible
