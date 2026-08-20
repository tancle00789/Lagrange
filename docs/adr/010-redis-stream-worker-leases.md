# ADR 010 — 基于 Redis Streams 的持久化 worker 投递

状态:已采纳(Phase 07)
日期:2026-08-05

## 背景

Redis 任务记录让状态持久了,Pub/Sub 让实时控制跨副本了,但执行仍依赖
接受请求的那个 API 进程创建的协程。进程丢失因此会让工作失去持有者。
Pub/Sub 无法修复这一点,因为它不保留未被消费的投递。

## 决策

- Redis 运行时模式把紧凑的 `JobEnvelope` 提交到命名空间化的 Redis
  Stream。消费者组把任务分发给嵌入在所有 API 副本中的 worker 循环。
  信封只含任务 ID、图输入与 LangGraph 配置;文档与检索内容不进入队列。
- 投递语义为至少一次。worker 仅在执行契约到达终态、或观察到持久的用户
  取消后,才用 `XACK` 确认。
- 图运行期间,持有者周期性地用 `XCLAIM` 刷新 pending entry 的空闲时间。
  配置租约到期后,其他 worker 用 `XAUTOCLAIM` 接管,从而在进程丢失后
  恢复工作。
- 优雅关闭取消在途执行,把 `running` 改为 `retrying`,并刻意不确认
  stream entry。租约到期后由新持有者恢复。
- 执行尝试次数持久化在任务记录上。瞬时失败把 `running -> retrying`;
  超过 `PK_AGENT_WORKER_MAX_ATTEMPTS` 后任务进入 `failed` 并确认投递。
- worker 并发、租约、轮询间隔、重试延迟与最大尝试次数都是配置项。内存
  运行时保留本地执行路径,供测试与单进程开发使用。

## 影响

- API 受理不再是任务的生命周期边界。任何健康副本都能执行或恢复任务,
  无需粘性路由。
- handler 必须幂等,因为崩溃可能发生在外部副作用之后、`XACK` 之前。
  既有的条件化任务迁移防止恢复的投递逆转 `cancelled`、`completed` 或
  `failed`。
- 队列使用短非阻塞读取加异步 sleep。这避免在兼容的 Redis 测试替身中
  阻塞事件循环,并把拾取延迟约束在 `PK_AGENT_WORKER_BLOCK_MS` 内。
- Redis AOF 提升持久性,但不是精确一次执行。运维按 worker 运维手册
  监控 pending entry 列表与接管延迟。
