# ADR-004: Prompt Engineering and Generation Architecture

**Date:** 2025-xx-xx  
**Status:** Accepted

## Context
Day 6 adds generation on top of the hybrid+rerank retrieval pipeline.
Key decisions: model choice, prompt structure, citation grounding, streaming.

## Decisions

### 1. Model: `mistral-small-latest`
- Free tier, sufficient context window for 5 parent chunks (~4–6K tokens)
- Upgrade path to `mistral-medium` or `mistral-large` for production without API changes

### 2. Parent chunks as context, not children
- Children (≈200 tokens) are used for retrieval precision
- Parents (≈800 tokens) are passed to LLM for full legal context
- Avoids mid-sentence truncation in generated citations

### 3. Citation-grounded system prompt
- Hard instruction: every claim must cite [Art. X DSGVO] or [§ Y BDSG]
- Language mirroring: answer in the same language as the question
- "Only use provided excerpts" prevents hallucination of non-retrieved articles

### 4. Streaming
- `client.chat.stream()` via context manager yields deltas
- `st.write_stream()` consumes the generator — UX feels responsive for legal answers
  that can run 300–500 tokens

### 5. Context formatting
- Numbered blocks [1]…[N] with heading + page number label
- Separator `---` between chunks to prevent cross-chunk bleed in attention

## Consequences
- Prompt token cost ~1.5–2K per query (context) + 300–500 output
- Citation accuracy depends on retrieval quality; the 3 persistent failures from
  Day 5 will surface as "insufficient information" responses — correct behaviour
- No conversation memory across turns (stateless per query); history is UI-only