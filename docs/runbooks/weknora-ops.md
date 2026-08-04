# WeKnora Subsystem Operations Runbook

## Layout

| Service | Role | Network | Host port |
|---|---|---|---|
| `weknora-app` | WeKnora Go API | `weknora-net` | `127.0.0.1:8081` |
| `weknora-docreader` | parsing gRPC sidecar | `weknora-net` | none |
| `weknora-postgres` | ParadeDB (vector + keyword) | `weknora-net` | none |
| `weknora-redis` | queue/cache | `weknora-net` | none |
| `app` (pk_agent) | only service bridging both networks | `pk-agent-net`, `weknora-net` | `8000` |

WeKnora source is build-mounted read-only from `../WeKnora`; the repo itself
is never modified.

## Bring-up

```bash
docker compose up -d --build
docker compose logs -f weknora-app        # wait for healthy
python scripts/init_weknora.py            # prints PK_AGENT_WEKNORA_API_KEY / _DEFAULT_KB_ID
export PK_AGENT_WEKNORA_API_KEY=...       # from script stdout
export PK_AGENT_WEKNORA_DEFAULT_KB_ID=...
docker compose up -d app                  # restart app with the key
bash scripts/smoke_test_weknora.sh        # optional real integration check
```

## Prerequisites

- An embedding model reachable from the `weknora-app` container. Default is
  Ollama on the host (`http://host.docker.internal:11434`, model `bge-m3`).
  Override with `WEKNORA_EMBEDDING_MODEL` / `WEKNORA_EMBEDDING_BASE_URL` /
  `WEKNORA_EMBEDDING_PROVIDER` before running `init_weknora.py`.
- Without a reachable embedding model, ingestion will fail at the embedding
  step and hybrid search returns nothing.

## Common failures

- **`knowledge_degraded` events / empty hits**: weknora-app down or slow.
  Check `docker compose ps weknora-app` and its logs. Tasks still complete
  (ADR 004 degradation policy).
- **Task fails with `KnowledgeAuthError`**: API key missing/revoked or
  `PK_AGENT_WEKNORA_DEFAULT_KB_ID` unset. Re-run `init_weknora.py` and restart
  `app` with fresh env.
- **Ingestion stuck in `pending`**: docreader not ready, or embedding model
  unreachable from the container. Verify
  `docker compose exec weknora-app curl -sf http://host.docker.internal:11434/api/tags`.
- **`hybrid-search` returns `{"data":null}` and logs `No retrievable indexing
  pipelines`**: `RETRIEVE_DRIVER` was unset on weknora-app, so no retrieval
  engine registered at boot. Compose now sets `RETRIEVE_DRIVER=postgres`; for
  a deployment that booted without it, add the env and restart, then repair
  the skipped conditional migrations (WeKnora's `000002_embeddings` is
  silently skipped when the driver is unset at first boot, yet recorded as
  applied). All three are idempotent:
  ```bash
  docker exec -i <weknora-postgres> psql -U weknora -d weknora \
    -c "SET app.skip_embedding='false';" \
    -f - < ../WeKnora/migrations/versioned/000002_embeddings.up.sql
  docker exec -i <weknora-postgres> psql -U weknora -d weknora \
    -f - < ../WeKnora/migrations/versioned/000007_embeddings_tag_id.up.sql
  docker exec -i <weknora-postgres> psql -U weknora -d weknora \
    -f - < ../WeKnora/migrations/versioned/000059_embeddings_hnsw_1024.up.sql
  ```
  Symptom of the missing table: ingestion fails with
  `relation "embeddings" does not exist` / `column "tag_id" does not exist`.
- **Key rotation**: create a new key via
  `POST /api/v1/tenants/{id}/api-keys` (see `scripts/init_weknora.py`), update
  `PK_AGENT_WEKNORA_API_KEY`, restart `app`. Keys are never logged by pk_agent.

## Data

- WeKnora data lives in the `weknora-pgdata` and `weknora-data-files` volumes.
  pk_agent checkpoints live in the `db` service — completely separate.
- `docker compose down -v` destroys both; there is no backup automation in
  Phase 2.
