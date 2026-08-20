# Phase 08 构建报告 —— 生产安全隔离

日期:2026-08-05

## 交付范围

- 新增异步 JWKS 校验:RS256/ES256 算法白名单、issuer/audience 精确
  校验、必需的时间/身份声明、TTL 缓存与密钥轮换刷新。
- 生产模式强制要求 JWKS 配置,同时保留显式的仅开发认证路径。
- 为所有 LangGraph checkpoint 数据表新增 PostgreSQL 强制 RLS 策略,以及
  事务级租户 saver 包装器。
- 新增队列信封的租户完整性校验,并限制租户标识符字符集,checkpoint
  前缀无法用分隔符伪造。
- 新增不可信 JSONL 证据框架、模型控制符中和、提示注入信号与测试。静态
  图路由与引用门控仍是主要的能力/输出边界。
- 新增 ADR 011 与身份/租户隔离运维手册。

## 验证

| 关卡 | 结果 |
|---|---|
| `uv lock` | ok,80 个包 |
| `ruff check --no-cache src tests` | ok |
| `mypy src` | ok,50 个源文件 |
| 安全/隔离聚焦测试 | 15 passed |
| 受限 WSL 沙箱外的完整 pytest | 100 passed |

## 显式边界

单元测试用 mock 的 JWKS 端点验证 RLS 包装器、租户拒绝、非对称签名与
轮换。作品集部署不要求真实 IdP,但真实 PostgreSQL RLS 与容器检查仍是
Phase 10 的强制证据。提示注入通过能力隔离与输出校验削减;没有任何
纯文本过滤被宣称为完整解决方案。
