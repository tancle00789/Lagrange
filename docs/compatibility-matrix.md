# 兼容性矩阵

| 组件 | Phase 1 目标 |
|---|---|
| Python | 3.12 |
| Node.js | 20.19+(Vite 8 构建要求) |
| 包管理器 | uv |
| Web 框架 | FastAPI + Uvicorn |
| 状态机 | LangGraph |
| Checkpoint | langgraph-checkpoint-postgres / 测试用 InMemorySaver |
| 数据校验 | Pydantic v2 |
| 生产身份认证 | 兼容 OIDC 的 HTTPS JWKS,RS256/ES256 |
| 数据库 | PostgreSQL 16 |
| Checkpoint 租户隔离 | PostgreSQL 强制 RLS + 事务级租户上下文 |
| 运行时任务存储 | Redis 7(AOF);测试/开发用内存实现 |
| 持久 worker 投递 | Redis Streams 消费者组,`XAUTOCLAIM`(Redis 6.2+) |
| 实时协调 | Redis Pub/Sub + 原子限流/ticket 键;测试/开发用内存实现 |
| Prompt 边界 | 不可信 JSONL 证据 + 静态工具路由 + 引用门 |
| 指标 | Prometheus 文本暴露与告警规则 |
| 链路追踪 | OpenTelemetry SDK + W3C 传播 + 可选 OTLP/HTTP exporter |
| 日志 | 带凭据脱敏的关联 JSON stdout |
| 对象存储 | 后续阶段引入 MinIO/S3 |
| 测试 | pytest + pytest-asyncio + FastAPI TestClient |
| Lint/类型 | ruff + mypy |
| 运行时打包 | Docker Compose |
