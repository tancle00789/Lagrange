# 可观测性运维

## 信号

- 指标：当 `PK_AGENT_METRICS_ENABLED=true` 时，通过 `GET /metrics` 暴露。
- 链路追踪：将 `PK_AGENT_OTLP_ENDPOINT` 设置为 collector 的完整 HTTP traces 端点，通常为 `http://otel-collector:4318/v1/traces`。
- 日志：以 JSON 格式输出到 stdout；通过容器平台常规的日志代理采集。

提供的 `ops/prometheus.yml` 会抓取 `app:8000`，`ops/prometheus-alerts.yml` 覆盖目标丢失、5xx 比例、p95 延迟、失败任务与长时间运行任务。根据观测基线调整阈值。

## 关联排查流程

1. 从告警标签与受影响的时间窗口入手。
2. 按 `route` 与 `status` 查找 HTTP 日志。
3. 从 API server 的 span 跟随 `trace_id` 到 `agent.job.execute`，即使 Redis Stream 消息被另一个副本消费。
4. 使用 `task_id` 检查持久化任务记录与 Redis pending entry。
5. 使用哈希后的租户值进行关联；不要将原始租户 ID 添加到指标标签中。

## 安全与失败行为

在网络/ingress 层保护 `/metrics`。它不包含任务输入或原始租户 ID，但会暴露运维拓扑。OTLP 导出是批量且可选的；未配置 collector 不会影响任务执行。切勿将授权头、SSE ticket、模型提示或证据内容作为 span 属性添加。
