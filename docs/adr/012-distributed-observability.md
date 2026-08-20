# ADR 012 — 关联日志、指标与分布式链路

状态:已采纳(Phase 09)
日期:2026-08-05

## 背景

Redis 让请求与任务不再绑定单一副本,但朴素日志无法把一次 API 提交关联
到 worker 执行。延迟、错误、任务结果与在途工作也没有稳定的指标契约。

## 决策

- 每个 HTTP 请求获得或校验一个 `X-Request-ID`;响应回显它。日志为 JSON
  格式,在相应上下文存在时自动携带 request ID、哈希后的租户、任务 ID、
  trace ID 与 span ID。
- 日志消息脱敏 bearer 凭据与 token/key/secret 查询值。敏感命名的结构化
  字段在序列化前被替换。
- 每个应用副本持有自己的 Prometheus registry,暴露在 `/metrics`。标签
  使用有界的路由模板而非原始路径/任务 ID。指标覆盖 HTTP 计数与时长、
  任务结果与时长、在途任务数。
- OpenTelemetry span 覆盖 HTTP 请求与 worker 执行。W3C trace 上下文注入
  持久任务信封,另一个副本可以接续提交方的链路。OTLP/HTTP exporter 仅在
  配置了 `PK_AGENT_OTLP_ENDPOINT` 时启用。
- Prometheus 抓取与告警规则在 `ops/` 下纳入版本管理。指标端点面向内网
  或入口策略保护,不对公网暴露。

## 影响

- 运维可以从一条告警走到路由、链路与任务,全程无需在日志中记录 bearer
  令牌或原始租户标识符。
- 指标按副本产出,设计上由 Prometheus 聚合。链路的采样/存储策略由配置
  的 collector/后端负责。
- 指标标签的基数有界;任务与请求标识符留在日志/链路中 —— 高基数关联
  本就属于那里。
