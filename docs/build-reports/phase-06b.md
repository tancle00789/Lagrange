# Phase 06B 构建报告 —— 分布式运行时协调

日期:2026-08-05

## 交付范围

- 新增跨 API 副本共享的 Redis 租户限流。
- SSE ticket 迁移到带 TTL 的 Redis key,用原子 `GETDEL` 消费。
- 新增按任务划分的 Redis Pub/Sub 事件投递,支撑跨副本 SSE。
- 每个运行时副本增加 Redis 取消控制监听器。路由到 app B 的取消请求现在
  能在提交共享 `cancelled` 状态后,取消 app A 持有的图协程。
- 图对 `EventBus` 协议做了泛化,同时保留内存实现供测试与本地开发。
- 新增 ADR 009 与五个跨实例协调测试。

## 验证

| 关卡 | 结果 |
|---|---|
| `ruff check src tests` | ok |
| `mypy src` | ok,48 个源文件 |
| 分布式协调聚焦测试 | 5 passed |
| 受限 WSL 沙箱外的完整 pytest | 85 passed |

## 显式边界

Redis Pub/Sub 提供实时协调,不是持久化执行。消息丢失时任务状态仍然
安全,但进程丢失后的自动 worker 重指派仍需要持久化队列/租约模型。
