# Phase 06 构建报告 —— 持久化任务运行时

日期:2026-08-05

## 交付范围

- 新增 `TaskStore` 抽象,含锁保护的内存实现与 Redis 实现。所有任务 API
  现在都经此边界读写。
- Redis 持久化小体量任务/报告记录、租户任务索引与哈希后的幂等映射,
  保留期可配置。除非选择 Redis 运行时存储且启动时可达,生产模式启动
  失败即关闭。
- 任务写入是条件化迁移。完成与失败只能从活跃状态写入,堵上了
  取消-完成竞态。
- API 进程保留自己持有的 LangGraph 任务句柄。DELETE 现在调用
  `asyncio.Task.cancel`;关闭也会终止持有的工作并把被中断的记录标记
  为失败。
- Compose 增加一个专用 AOF Redis 服务与卷,与 WeKnora 的 Redis 子系统
  相互独立。
- 新增 ADR 008、运行时配置示例,以及基于 `fakeredis` 的内存/Redis/
  取消测试。

## 验证

| 关卡 | 结果 |
|---|---|
| 临时 Linux 验证器中 `uv lock` + frozen sync | ok,80 个包(含 Starlette TestClient 的 `httpx2`) |
| `ruff format --check`(6 个 Phase 05/06 安全/运行时文件) | ok |
| `ruff check src tests` | ok |
| `mypy src` | ok,47 个源文件 |
| 运行时/安全聚焦测试 | 10 passed —— 内存 store、Redis store、幂等、条件迁移、真实协程取消、生产配置与 SSE/JWT 控制 |
| 测试收集 | 收集到 80 个测试 |
| Compose YAML 解析 | ok |
| 受限 WSL 沙箱外的完整 `pytest` | ok,80 passed |

最初的挂起被隔离定位为受管沙箱无法跨线程唤醒 asyncio 循环;一个标准库
`asyncio.to_thread` 复现器在沙箱内失败、在沙箱外通过。诊断记录在
`docs/runbooks/wsl-test-hangs.md`。Docker/真实 Redis smoke 未运行,因为
当前 WSL 环境没有 Docker;Redis 行为由 `fakeredis` 覆盖。

## 显式边界

共享 Redis 状态让取消跨副本成为终态,但立即停止计算目前只在请求打到
持有协程的进程时有效。分布式 worker 与取消控制频道属于 Phase 06B。控制
频道随后已在 ADR 009 交付;持久化 worker 重指派仍待完成。
