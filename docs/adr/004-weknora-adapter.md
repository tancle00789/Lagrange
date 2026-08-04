# ADR 004: WeKnora as a Contained Knowledge Subsystem

## Status
Accepted (Phase 2)

## Context
Phase 1 stubbed `KnowledgeProvider`. Phase 2 must bring real retrieval into the
agent without violating the core constraints: LangGraph is the only task state
machine, business code must not touch the WeKnora database, and graph state
must never hold large text blobs.

## Decision
WeKnora runs as a **contained subsystem** inside our docker-compose stack:
`weknora-app`, `weknora-docreader`, `weknora-postgres` (ParadeDB/pgvector),
`weknora-redis` on an isolated `weknora-net` network. The pk_agent `app`
service is the only component attached to both networks.

- **Data ownership**: documents, chunks, embeddings and KB metadata belong to
  WeKnora's own PostgreSQL (`weknora-pgdata` volume). pk_agent never connects
  to it; access is exclusively through the WeKnora HTTP API
  (`X-API-Key` scoped keys).
- **Transport boundary**: `providers/weknora_client.py` is the only module that
  knows WeKnora URLs, auth headers and response envelopes. It maps every
  failure to `providers/errors.py` types — nothing is swallowed.
- **Contract boundary**: `providers/weknora.py` maps raw JSON to Pydantic
  contract models (`providers/contract.py`). LangGraph nodes only ever see
  `KnowledgeHit`/`KnowledgeChunk`. Snippets are truncated
  (`weknora_snippet_max_chars`, default 500) in the provider so state cannot
  receive full documents.
- **Tenant mapping**: pk_agent `tenant_id` maps 1:1 to a WeKnora KB via
  `weknora_tenant_kb_map` with `weknora_default_kb_id` fallback. No
  cross-tenant fallback exists; an unmapped tenant is a hard
  `KnowledgeBadRequest`.
- **Retries**: tenacity, max 3 attempts, exponential backoff capped at 8s,
  only for connection errors and 5xx. 4xx never retried. Timeouts map to
  `KnowledgeTimeout`.
- **Degradation policy**: `retrieve_knowledge` graph node treats
  `KnowledgeUnavailable`/`KnowledgeTimeout` as degradable — the task continues
  with empty hits and records `knowledge_error`. `KnowledgeAuthError`
  propagates and fails the task, because a bad key is misconfiguration and
  must not be silently tolerated.
- **WeKnora's own agent engine** (`internal/agent`) is not used. It remains
  available inside the subsystem but pk_agent never invokes it; LangGraph is
  the only orchestrator.

## Consequences
- WeKnora upgrades are isolated behind the contract layer; only
  `weknora_client.py` should need changes.
- Retrieval outage degrades answer quality but not task completion; operators
  must watch for `knowledge_degraded` events.
- The subsystem needs a reachable embedding model (default: Ollama on host).
  Bootstrap is codified in `scripts/init_weknora.py`.
