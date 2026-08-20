# Phase 05 构建报告 —— 生产认证边界

日期:2026-08-05

## 交付范围

- 生产运行时校验拒绝默认/过短的 JWT secret,并禁用 `dev:` bearer 令牌。
  签名 JWT 现在必须包含 `exp`、`sub` 与 `tenant_id`,身份与角色声明有
  显式类型校验。
- 用经认证的 SSE ticket 交换替换了浏览器长寿命 `?token=<bearer>`
  EventSource URL。ticket 为密码学随机,绑定任务与租户,默认 60 秒
  过期,一次性使用。
- 按租户隔离的任务幂等 key 堵上了跨租户任务 ID 泄露;超长 key 被拒绝。
  缺少角色现在返回 HTTP 403。
- 前端在打开 EventSource 前透明地请求 SSE ticket,并在任务事件时间线上
  报告 ticket 交换失败。
- 在一次干净安装发现开发服务器存在一个高危与一个中危公告后,把
  Vite 5 升级到 8.2、React 插件 4 升级到 6。升级后的干净依赖树报告
  零已知漏洞;Docker 现在钉住所需的 Node 20.19 基线。
- 新增 ADR 007,更新威胁模型与配置示例。

## 验证

| 关卡 | 结果 |
|---|---|
| `ruff format --check`(10 个改动的 Python 文件) | ok |
| `ruff check src tests` | ok |
| `mypy src` | ok,46 个源文件 |
| `tests/unit/test_security.py` | 4 passed |
| 直接异步 API 安全 smoke | ok —— 403 RBAC、租户级幂等、跨租户 ticket 拒绝、一次性重放拒绝 |
| `npm run build`(Node 20.19.4,干净锁定的 Vite 8 安装) | ok |
| 升级后 `npm audit` | ok,0 漏洞 |
| 临时 WSL 验证器中的完整 `pytest` | 最初被受管沙箱的跨线程 asyncio 唤醒阻塞;在 Phase 06 诊断期间解决。扩展后的 80 测试套件现在在受限沙箱外通过。 |

此前 Phase 4' 基线为 67 个通过测试;本阶段新增 7 个逻辑测试用例。已解决
的验证器问题见 `docs/runbooks/wsl-test-hangs.md`。

## 延后

- 非对称 JWT/JWKS 校验与轮换。
- Redis 支撑的分布式限流与共享 SSE/任务状态。
- PostgreSQL RLS 或按租户分 schema 的 checkpoint 隔离。
