# ADR 004:WeKnora 作为受控知识子系统

## 状态
已采纳(Phase 2)

## 背景
Phase 1 打桩了 `KnowledgeProvider`。Phase 2 必须把真实检索引入 agent,同时不违反核心约束:LangGraph 是唯一的任务状态机,业务代码不得触碰 WeKnora 数据库,图 state 绝不存放大段文本。

## 决策
WeKnora 作为**受控子系统**运行在我们的 docker-compose 栈内:
`weknora-app`、`weknora-docreader`、`weknora-postgres`(ParadeDB/pgvector)、
`weknora-redis`,位于隔离的 `weknora-net` 网络。pk_agent 的 `app`
服务是唯一同时接入两个网络的组件。

- **数据归属**:文档、chunk、embedding 与知识库元数据归属 WeKnora 自己的
  PostgreSQL(`weknora-pgdata` 卷)。pk_agent 永不连接它;访问只能经由
  WeKnora HTTP API(限定作用域的 `X-API-Key`)。
- **传输边界**:`providers/weknora_client.py` 是唯一知晓 WeKnora URL、
  认证头与响应封套的模块。它把每一种失败映射为 `providers/errors.py`
  中的类型 —— 不吞掉任何错误。
- **契约边界**:`providers/weknora.py` 把原始 JSON 映射为 Pydantic 契约
  模型(`providers/contract.py`)。LangGraph 节点只会看到
  `KnowledgeHit`/`KnowledgeChunk`。片段在 provider 内截断
  (`weknora_snippet_max_chars`,默认 500),state 无法收到完整文档。
- **租户映射**:pk_agent 的 `tenant_id` 经 `weknora_tenant_kb_map` 1:1
  映射到 WeKnora 知识库,以 `weknora_default_kb_id` 兜底。不存在跨租户
  回退;未映射的租户直接报硬错误 `KnowledgeBadRequest`。
- **重试**:tenacity,最多 3 次,指数退避上限 8s,仅针对连接错误与
  5xx。4xx 绝不重试。超时映射为 `KnowledgeTimeout`。
- **降级策略**:`retrieve_knowledge` 图节点把
  `KnowledgeUnavailable`/`KnowledgeTimeout` 视为可降级 —— 任务以空命中
  继续并记录 `knowledge_error`。`KnowledgeAuthError` 向上传播并让任务
  失败,因为坏 key 属于配置错误,绝不能被静默容忍。
- **WeKnora 自带的 agent 引擎**(`internal/agent`)不使用。它在子系统内
  仍然可用,但 pk_agent 永不调用它;LangGraph 是唯一的编排器。

## 影响
- WeKnora 升级被隔离在契约层之后;只有 `weknora_client.py` 需要改动。
- 检索故障会降级回答质量,但不影响任务完成;运维必须关注
  `knowledge_degraded` 事件。
- 子系统需要一个可达的 embedding 模型(默认:宿主机上的 Ollama)。
  引导流程固化在 `scripts/init_weknora.py` 中。
