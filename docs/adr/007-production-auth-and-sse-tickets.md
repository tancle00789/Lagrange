# ADR 007 — 生产认证与 SSE Ticket

状态:已采纳(Phase 05)
日期:2026-08-05

## 背景

浏览器此前在 EventSource 查询串中使用长寿命 bearer 令牌,因为原生
EventSource API 无法设置 Authorization 头。查询串中的凭据可能被浏览器
历史、访问日志与反向代理留存。认证解析器此前在所有环境中都接受开发
令牌、接受没有过期时间的签名令牌,且除非运维显式修改,否则使用默认
JWT secret。

任务幂等 key 此前全局存储而非按租户隔离,两个租户使用相同 key 时可能
解析到先创建租户的任务。随后的任务读取仍做租户校验,但创建响应可能
泄露另一个任务的标识符。

## 决策

- `dev:{tenant}:{user}:{roles}` bearer 令牌仅在非 `production` 环境中
  被接受。生产启动拒绝默认 JWT secret 以及短于 32 字符的 secret。
- HS256 令牌必须包含 `exp`、`sub` 与 `tenant_id`。tenant、subject 与
  roles 声明在构造 `Principal` 之前先做类型校验。
- 已认证客户端从 `POST /api/v1/tasks/{task_id}/events/ticket` 获取 SSE
  ticket。随机 ticket 默认 60 秒过期,绑定到调用方的租户与任务,并在
  首次连接尝试时被移除。只有这个 ticket 进入 SSE 查询串;bearer 令牌
  保留在 Authorization 头中。
- 当前 ticket 存储是进程内的。这与当前进程内的任务/事件注册表一致;
  两者在 API 水平扩容前都必须迁移到共享基础设施。
- 幂等 key 按 `(tenant_id, key)` 索引,上限 200 字符;RBAC 失败返回
  HTTP 403 而不是逃逸为服务器错误。

## 影响

- 现有浏览器客户端必须在打开 EventSource 之前请求 ticket;随附前端已
  自动完成这一交换。
- 断开的 EventSource 需要新 ticket,而不是重放旧 URL。现有任务轮询仍是
  当前 UI 的兜底。
- 反向代理仍应脱敏 `ticket` 查询参数。短寿命与一次性降低了暴露面,但
  并不意味着记录它是个好主意。
- 多副本部署需要共享的任务/事件/ticket 层或粘性路由;Redis 协调属于
  后续阶段。
