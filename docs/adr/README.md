# Architecture Decision Records

This directory contains ADRs (Architecture Decision Records) for the
Munich RAG Compliance project. Format adapted from
[Michael Nygard's original](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions),
deliberately compressed for portfolio/sprint pace.

## Index

| # | Title | Status | Date |
|---|-------|--------|------|
| 001 | Hierarchical parent-child chunking with context-prefix injection | Accepted | 2026-05-10 |
| 002 | Embedding model and vector store selection | Accepted | 2026-05-11 |
| 003 | Hybrid search strategy and reranker selection | Accepted | 2026-05-13 |

## Conventions

- One ADR per significant, non-default architectural choice.
- ADRs are written **the same day** the decision is made — never backfilled
  more than 24 hours later.
- Lightweight format — six sections, fits on one screen.
- Decisions about defaults (using Pydantic, structlog, uv, etc.) do not
  warrant ADRs.

## Related

- See [`../lessons-learned.md`](../lessons-learned.md) for postmortems on
  concrete bugs encountered and how they were resolved.

