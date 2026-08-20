# 阶段 09 构建报告 — 分布式可观测性

Date: 2026-08-05

## 交付范围

- 新增结构化 JSON 日志，包含 request/task/tenant-hash、trace/span 上下文以及凭据脱敏。
- 新增请求 ID 校验/透传和受限路由模板指标。
- 新增每个副本的 Prometheus HTTP/Job 计数器、延迟直方图以及 `/metrics` 端点的活跃任务 gauge。
- 新增 OpenTelemetry 服务端与消费者 span，W3C 上下文通过跨副本的持久化 Redis 任务信封携带。
- 新增可选 OTLP/HTTP 导出、Prometheus 抓取配置、告警规则、ADR 012 以及运维 runbook。

## 验证

| 门禁 | 结果 |
|---|---|
| Dependency lock | ok, 89 packages |
| `ruff check --no-cache src tests` | ok |
| `mypy src` | ok, 51 source files |
| 聚焦可观测性/运行时测试 | 11 passed |
| 在受限 WSL 沙箱外完整 pytest | 103 passed |

测试覆盖凭据脱敏、关联字段、trace ID、trace-context 注入、受限指标标签、请求 ID 回显以及任务指标/gauge 行为。

## 明确边界

本仓库提供信号生成与告警契约。已部署的 Prometheus 和 OTLP collector/backend 属于环境层面事项，将在最终 Docker 部署可用时进行验证。
