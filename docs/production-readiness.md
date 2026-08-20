# 生产就绪清单

## 已被自动化测试证明

- [x] 租户级 API 读取、列表、幂等与终态转换。
- [x] Redis 共享限流、SSE ticket、事件流与取消。
- [x] Stream ACK、重试、停机恢复、崩溃租约接管与有界任务重试次数。
- [x] JWKS 算法/声明校验与未知密钥的轮换刷新。
- [x] Checkpoint 租户包装器在执行 SQL 前拒绝跨租户配置。
- [x] Prompt 证据框架、控制符中和与引用门。
- [x] 凭据脱敏的关联日志、路由安全指标与 trace carrier。
- [x] Chromium 中的浏览器令牌/任务/报告流程。

## 每次部署必须执行

- [ ] 针对真实容器运行 `scripts/verify_core_deployment.sh`。
- [ ] 确认三条强制 RLS 策略生效,且无未带租户上下文的 checkpoint 行。
- [ ] 演练 IdP staging 密钥、issuer/audience 与一次完整的轮换重叠期。
- [ ] 将 `/metrics` 与 PostgreSQL/Redis 限制在内网。
- [ ] 配置告警路由与 OTLP 后端;验证一条测试告警/链路。
- [ ] 通过平台 secret 存储注入密钥,并对仓库/镜像做密钥泄露与漏洞扫描。
- [ ] 为 PostgreSQL、Redis AOF 与 WeKnora 制定备份/恢复目标。
- [ ] 针对模型与 provider 容量对 worker 并发做压测。
- [ ] 启用外部写工具前,确认其使用幂等键。

## 需要留存的发布证据

- Git commit 与依赖锁文件哈希。
- Ruff、mypy、pytest、前端构建与 Playwright 的输出。
- 容器镜像 digest 与漏洞扫描结果。
- 部署验证器输出、RLS 策略查询与 Redis `XINFO`/`XPENDING`。
- IdP 轮换测试、备份恢复测试与告警送达证据。
