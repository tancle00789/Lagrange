# ADR 008 — 持久化任务控制面与取消语义

状态:已采纳(Phase 06)
日期:2026-08-05

## 背景

任务记录与幂等映射此前存在进程内字典中。重启 API 会抹掉任务可见性,
多个副本也互相看不到对方的记录。`DELETE /tasks/{id}` 改变了展示状态,
但并不取消正在运行 LangGraph 的 `asyncio.Task`,因此迟到的图结果可能
把 `cancelled` 覆盖回 `completed`。

LangGraph checkpoint 在 PostgreSQL 中已经是持久的,但 HTTP 控制面没有
把租户关联到这些执行的持久索引。

## 决策

- 引入异步 `TaskStore` 边界,含内存与 Redis 两种实现。内存仍是测试/
  单进程默认;生产启动要求 `PK_AGENT_RUNTIME_STORE=redis`。
- Redis 保存有界的 JSON 任务记录、按租户排序的任务索引,以及经
  SHA-256 哈希的租户/幂等映射。任务记录默认保留七天,幂等映射保留
  一天。
- 状态写入支持期望源状态校验:内存实现用锁,Redis 用 WATCH 事务。图
  结果只能从 `running` 写入;无法覆盖并发的终态。
- 每个 API 进程追踪自己持有的 `asyncio.Task`。取消先把活跃状态原子
  迁移为 `cancelled`,再对本地持有的协程调用 `cancel()`。关闭时取消
  持有的工作,并把被中断的运行记为失败,而不是无限期停留在 running。
- Docker Compose 增加一个 pk-agent 专用的 append-only Redis 服务。它与
  WeKnora 的私有 Redis 子系统相互独立。

## 影响

- 任务列表、状态与幂等创建在配置保留期内可跨 API 重启存活。
- 本地取消现在会真正停止模型/检索的 await,且无法被迟到的完成写入
  逆转。
- 如果取消请求打到另一个副本,共享状态仍会变为 `cancelled` 并拒绝迟到
  的完成写入,但持有计算的副本暂时无法被通知立即释放算力。完整的
  分布式取消需要共享 worker 队列/控制频道。
- Redis 目前只存小体量任务/报告元数据。文档与 PDF 仍归 WeKnora 所有;
  图 checkpoint 仍归 PostgreSQL 所有。
