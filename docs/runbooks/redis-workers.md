# Redis Worker 运维

## 运行时约定

默认 stream 为 `pk_agent:jobs`，消费组为 `pk_agent:workers`；如配置了 `PK_AGENT_RUNTIME_KEY_PREFIX`，则将 `pk_agent` 替换为该前缀。投递语义为至少一次（at-least-once）。只要其所有者持续刷新租约，pending entry 就是健康的；超过 `PK_AGENT_WORKER_LEASE_MS` 后变为可恢复状态。

## 检查队列

```bash
redis-cli XINFO GROUPS pk_agent:jobs
redis-cli XPENDING pk_agent:jobs pk_agent:workers
redis-cli XINFO CONSUMERS pk_agent:jobs pk_agent:workers
```

关注 `pending`、consumer 的 `idle` 以及最早的 pending entry。pending 持续增长，或 entry 超过多个租约间隔仍未处理，表明存在 worker、Redis 连接或下游 provider 问题。

## 恢复行为

1. 优雅关闭会将任务记录变为 `retrying`，并保留其 stream entry 为 pending 状态。
2. 硬崩溃会保持任务的最后状态不变，通常为 `running`。
3. 健康的 worker 在租约到期后调用 `XAUTOCLAIM`，递增持久化的尝试次数并恢复执行。
4. 终态结果或持久的用户取消会触发 `XACK`。

在人工执行 `XACK` 之前，务必先检查对应的任务记录。手动确认会丢弃其自动恢复路径。如果 Redis 不可用，任务创建会失败，而不是假装任务已入队。

## 配置建议

- 租约应显著高于正常的事件循环与 Redis 延迟。
- 轮询间隔应足够低，以满足可接受的队列拾取延迟。
- 并发度应按下游模型/provider 容量来设定，而非仅按 CPU 核数。
- 将最大尝试次数视为错误预算决策；重试可以重复幂等读取，但外部写入需要自带幂等键。
