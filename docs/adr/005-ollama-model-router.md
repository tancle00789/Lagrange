# ADR 005: Ollama as the first real model provider behind ModelRouter

## Status

Accepted (Phase 3)

## Context

Phases 1–2 ran only `FakeModel`. Phase 3 introduces grounded answer generation,
which needs a real LLM for meaningful end-to-end runs, while tests must never
call a real model (hard constraint). The runtime must also work fully offline:
no external API keys, no public-network dependency in the default path.

## Decision

- `ModelRouter` routes by **capability** (e.g. `"answer"`), not by vendor.
- The `Model` protocol is `async def generate(prompt, *, capability) -> str`.
- `OllamaModel` (`harness/ollama.py`) is the first real provider: it calls a
  host-run Ollama (`POST {base}/api/chat`, `stream: false`) with a configurable
  timeout and a single retry on transport/timeout errors only.
- Wiring lives in `agentos/main.py` lifespan: if `PK_AGENT_OLLAMA_CHAT_MODEL`
  is set, capability `"answer"` gets `OllamaModel`; otherwise `FakeModel`.
  Tests and CI leave it unset, so they never touch a real model.
- The model is prompted for strict JSON (`PromptAssembler`), but the graph
  never trusts it: `_parse_model_output` defensively strips code fences, falls
  back to plain text on parse failure, and the `citation_gate` node drops any
  chunk_id that was not actually retrieved.

## Consequences

- Real-model runs need a one-time `ollama pull qwen3:4b` on the host; compose
  reaches the host via `host.docker.internal` (`extra_hosts: host-gateway`).
- Offline/air-gapped deployments are supported by construction (local model).
- Swapping in a hosted provider later is a new `Model` implementation plus a
  wiring change in `main.py`; graph code does not change.
- LLM nondeterminism is contained behind the citation gate: an eloquent but
  ungrounded answer degrades to synthesized claims or `grounded=False`, never
  to fabricated citations.
