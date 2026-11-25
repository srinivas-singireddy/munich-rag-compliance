# ADR-012: Configuration vs Code Constants

**Date:** 2026-11-25
**Status:** Accepted
**Deciders:** Srinivas Singireddy
**Context:** D3 — Config externalisation (prerequisite for container, CI and EKS deployment)

---

## Context

Environment-specific values were hard-coded across the code: Qdrant at
`localhost:6333` (`vector_store.py`), the collection name, the Mistral model
(`mistral-small-latest`, in two files), and the Mistral key read directly from
`os.environ` at import time. The fastembed cache defaulted to macOS `$TMPDIR`
(L-017). None of this can run unchanged on a laptop, in CI and on EKS.

At the same time, the codebase holds tuning values that define tested behaviour:
40 hybrid candidates, top 5 after reranking, 8 worker threads, and the embedding,
sparse and reranker model names. The locked figures (P@1 80%, R@5 88%,
MRR 0.828) were measured with exactly these values.

## Decision

**Configuration (pydantic-settings, `src/config.py`) holds what differs between
environments:**

| Setting | Default |
|---|---|
| `qdrant_host` / `qdrant_port` | `localhost` / `6333` |
| `qdrant_collection` | `munich_rag_children` |
| `mistral_api_key` | none — `SecretStr`, never logged |
| `mistral_model` | `mistral-small-2603` (pinned, not `-latest`) |
| `llm_timeout_seconds` | `30` |
| `model_cache_dir` | `~/.cache/munich-rag/models` |

Values come from environment variables or `.env` (git-ignored); `.env.example`
documents every setting.

**Code constants hold what defines tested behaviour:** candidate count, top-N,
worker ceiling, and the embedding / sparse / reranker model names. Changing them
must go through code review and the eval gate — not an environment variable.

### Supporting decisions

- **Fail fast on a missing key.** The agent raises a clear `RuntimeError` at
  startup if `MISTRAL_API_KEY` is unset. On Kubernetes a missing Secret then
  shows as a failing pod immediately, not as a healthy pod that fails on the
  first question. Search-only scripts do not import the agent and run without
  a key.
- **Pin the LLM version.** `-latest` aliases change silently when the provider
  releases a new model, so identical code could produce different answers.
- **Embedding models stay in code** because the index was built with them;
  changing one without re-indexing silently breaks retrieval.
- **Single source of truth** for the embedding model name (`tokenization.py`
  imports it from `encoder.py`).

## Alternatives considered

- **Everything configurable** — rejected: tuning values could change via an
  environment variable, bypassing the eval gate and invalidating the figures.
- **Read `os.environ` directly** — rejected: untyped, no defaults, no secret
  masking, scattered across files.
- **Lazy client creation (no startup check)** — rejected for the agent service:
  hides a missing secret until the first request.

## Consequences

- Same code runs on laptop, CI and EKS; only environment variables differ.
- Verified: retrieval eval unchanged (80% / 88% / 0.828) and fully offline
  (`HF_HUB_OFFLINE=1`, zero downloads).
- The key moved from the shell profile (`~/.zshrc`) to project-scoped `.env`.
- `mistral-small-2603` is the new default model; it gets validated when the
  agent runs end to end again (API credits pending).
- Not configurable yet: OpenTelemetry settings (added with ADR-010) and an
  OpenAI-compatible base URL for switching LLM providers (deferred).