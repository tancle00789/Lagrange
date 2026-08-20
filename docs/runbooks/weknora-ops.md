# WeKnora 子系统运维手册

## 布局

| 服务 | 角色 | 网络 | 主机端口 |
|---|---|---|---|
| `weknora-app` | WeKnora Go API | `weknora-net` | `127.0.0.1:8081` |
| `weknora-docreader` | 解析 gRPC sidecar | `weknora-net` | 无 |
| `weknora-postgres` | ParadeDB（向量 + 关键词） | `weknora-net` | 无 |
| `weknora-redis` | 队列/缓存 | `weknora-net` | 无 |
| `app`（pk_agent） | 唯一同时桥接两个网络的服务 | `pk-agent-net`、`weknora-net` | `8000` |

WeKnora 源码通过只读方式 build-mount 自 `../WeKnora`；仓库本身不会被修改。

## 启动

```bash
docker compose up -d --build
docker compose logs -f weknora-app        # 等待 healthy
python scripts/init_weknora.py            # 输出 PK_AGENT_WEKNORA_API_KEY / _DEFAULT_KB_ID
export PK_AGENT_WEKNORA_API_KEY=...       # 来自脚本 stdout
export PK_AGENT_WEKNORA_DEFAULT_KB_ID=...
docker compose up -d app                  # 用 key 重启 app
bash scripts/smoke_test_weknora.sh        # 可选的真实集成检查
```

## 前置条件

- `weknora-app` 容器必须能访问一个嵌入模型。默认使用宿主机上的 Ollama（`http://host.docker.internal:11434`，模型 `bge-m3`）。运行 `init_weknora.py` 前，可通过 `WEKNORA_EMBEDDING_MODEL` / `WEKNORA_EMBEDDING_BASE_URL` / `WEKNORA_EMBEDDING_PROVIDER` 覆盖。
- 如果嵌入模型不可达，ingestion 会在嵌入步骤失败，且混合搜索不会返回结果。

## 常见故障

- **`knowledge_degraded` 事件 / 空命中**：weknora-app 宕机或响应慢。检查 `docker compose ps weknora-app` 及其日志。任务仍会完成（ADR 004 降级策略）。
- **任务失败并报 `KnowledgeAuthError`**：API key 缺失/撤销，或 `PK_AGENT_WEKNORA_DEFAULT_KB_ID` 未设置。重新运行 `init_weknora.py`，并用新的环境变量重启 `app`。
- **Ingestion 卡在 `pending`**：docreader 未就绪，或容器内无法访问嵌入模型。验证 `docker compose exec weknora-app curl -sf http://host.docker.internal:11434/api/tags`。
- **`hybrid-search` 返回 `{"data":null}` 并日志输出 `No retrievable indexing pipelines`**：`RETRIEVE_DRIVER` 在 weknora-app 上未设置，因此启动时没有注册检索引擎。Compose 现在已设置 `RETRIEVE_DRIVER=postgres`；对于未设置该变量就启动的部署，需添加该环境变量并重启，然后修复被跳过的条件迁移（WeKnora 的 `000002_embeddings` 在首次启动时驱动未设置会被静默跳过，但仍被记录为已应用）。以下三条都是幂等的：
  ```bash
  docker exec -i <weknora-postgres> psql -U weknora -d weknora \
    -c "SET app.skip_embedding='false';" \
    -f - < ../WeKnora/migrations/versioned/000002_embeddings.up.sql
  docker exec -i <weknora-postgres> psql -U weknora -d weknora \
    -f - < ../WeKnora/migrations/versioned/000007_embeddings_tag_id.up.sql
  docker exec -i <weknora-postgres> psql -U weknora -d weknora \
    -f - < ../WeKnora/migrations/versioned/000059_embeddings_hnsw_1024.up.sql
  ```
  缺少表的症状：ingestion 失败并提示 `relation "embeddings" does not exist` / `column "tag_id" does not exist`。
- **Key rotation**：通过 `POST /api/v1/tenants/{id}/api-keys` 创建新 key（参见 `scripts/init_weknora.py`），更新 `PK_AGENT_WEKNORA_API_KEY`，重启 `app`。pk_agent 不会记录 key。

## 数据

- WeKnora 数据保存在 `weknora-pgdata` 与 `weknora-data-files` 卷中。pk_agent checkpoint 保存在 `db` 服务中 —— 两者完全隔离。
- `docker compose down -v` 会销毁两者；Phase 2 没有自动备份机制。
