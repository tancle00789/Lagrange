# ADR 002:PostgreSQL checkpoint

## 状态
Phase 1 已采纳。

## 背景
Phase 1 必须证明运行状态可持久化。目标生产栈包含 PostgreSQL。

## 决策
使用 `langgraph-checkpoint-postgres` 的 `AsyncPostgresSaver`。`thread_id` 为 `{tenant_id}:{task_id}`;`checkpoint_ns` 来自 `PK_AGENT_CHECKPOINT_NS`。

## 影响
- 本地测试设置 `PK_AGENT_DATABASE_URL=memory://` 仍可使用 `InMemorySaver`。
- Docker Compose smoke 测试验证真实 PostgreSQL checkpoint 配置。
- 生产加固仍需租户级 RLS 或按租户分 schema。
