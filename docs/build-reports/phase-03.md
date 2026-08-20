# Phase 03 构建报告 —— 最小知识工作流 + Web UI

日期:2026-08-04

## 交付范围

- 研究图替换 echo 图:
  `normalize → retrieve_internal → generate_grounded_answer → citation_gate → finalize`
  (`src/pk_agent/graph/research_graph.py`)。echo 图已删除。
- 异步模型层:`Model.generate` 改为 async;`ModelRouter` 按能力路由
  (`"answer"`)。新增 `OllamaModel`(`harness/ollama.py`)调用宿主机
  Ollama `/api/chat`,带超时 + 单次传输重试。ADR 005。
- `PromptAssembler.build_answer_prompt` 要求严格 JSON
  `{answer, claims:[{text, chunk_ids}]}`;`_parse_model_output` 剥除代码
  围栏,解析失败降级为纯文本。
- `citation_gate` 丢弃不在 `knowledge_hits` 中的 chunk_id(计入
  `dropped_citations`),模型没有产出任何 claim 时从 top hit 合成一条,
  未检索到证据时标记 `grounded=False`。
- 领域模型(`domain/research.py`):Evidence / Claim / Report。图 state
  只携带小对象(≤500 字符片段,≤300 字符 claim)。
- API:`GET /api/v1/tasks`(按租户、最新在前、上限 100)、
  `TaskStatusResponse` 上的 `report`、对 Vite 开发源开放 CORS、从
  `/app/static`(开发时 `frontend/dist`)托管静态 SPA。
- SSE 认证:`get_current_principal` 增加 `?token=` 查询回退
  (EventSource 无法设置请求头;威胁模型记录了仅限开发的权衡)。
- 前端(`frontend/`,React 18 + Vite 5 + TS,暗色主题,无组件库):
  TokenGate、TaskForm(含 require_approval)、TaskList(5 秒轮询)、
  TaskDetail(SSE 事件时间线、知识命中、带逐条引用有效性的报告、
  经 resume 批准/拒绝、取消)。多阶段 Dockerfile 把 SPA 构建进 app
  镜像;不增加 compose 服务。

## 验证(全部为真实验证,本机)

| 关卡 | 结果 |
|---|---|
| `uv sync --frozen` | ok |
| `ruff check src tests` | ok |
| `mypy src` | ok,32 个文件 |
| `pytest` | 28 passed(研究图 happy/暂停/解析降级/引用门分支、provider 契约、检索降级、API 含列表 + 批准流) |
| `npm run build`(前端) | ok(tsc + vite) |
| `docker compose up -d --build app` | ok;SPA 服务于 `http://localhost:8000/` |
| `scripts/smoke_test.sh` | ok |
| 真实 LLM:`PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b` + `scripts/smoke_test_weknora.sh` | ok —— `knowledge_hits` 中有真实 chunk、报告有据可依;qwen3:4b 产出答案,1 条已验证 claim,0 条被丢弃引用 |
| SSE `?token=` 认证 | 经 curl 验证(收到 `snapshot` 事件) |

关于 ollama 的说明:`registry.ollama.ai` 不可达(TLS 超时),因此
qwen3:4b 经 ModelScope(`modelscope.cn/unsloth/Qwen3-4B-GGUF:Q4_K_M`)
拉取并本地打标为 `qwen3:4b`。Docker 基础镜像同样经 DaoCloud 镜像站。

## 本阶段发现并修复的事故

1. **Phase 2 的 smoke 是假阳性。** 它断言标记串出现在 `result` 中,但
   echo 图会回显查询(其中包含标记串),因此检索静默坏掉时它照样通过。
   Phase 3 的断言改为检查 `knowledge_hits` 与 `report.grounded` ——
   smoke 现在真正证明检索发生。
2. **WeKnora 检索自 Phase 2 起一直静默失效。** weknora-app 上没有设置
   `RETRIEVE_DRIVER`,没有注册任何检索引擎,`hybrid-search` 总是以
   HTTP 200 返回 `{"data":null}`。已在 compose 中修复
   (`RETRIEVE_DRIVER=postgres`)。
3. **被跳过的条件迁移。** WeKnora 的 `000002_embeddings`(以及 `000007`
   的 tag_id 列)由 `app.skip_embedding` 门控,首次启动时它为 true;
   golang-migrate 仍把它们记录为已应用。通过手动执行幂等 SQL 修复;
   已记录在 `docs/runbooks/weknora-ops.md`。

## 未做 / 注意事项

- SPA 的浏览器点击走查在 HTTP 层面做了验证(SPA 可访问,API + SSE +
  认证可用);视觉交互应在 `http://localhost:8000/` 做一次人工抽查。
- CPU 上的 qwen3:4b 每次回答约 10–60 秒;smoke 轮询预算 30 秒,冷模型
  时可能需要重试。
- `?token=` SSE 认证仅限开发(见威胁模型 Phase 3 节)。
