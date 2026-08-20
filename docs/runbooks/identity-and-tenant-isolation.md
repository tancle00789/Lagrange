# 身份与租户隔离运维

## 生产环境身份配置

设置以下所有项：

```text
PK_AGENT_ENVIRONMENT=production
PK_AGENT_JWT_JWKS_URL=https://identity.example.com/.well-known/jwks.json
PK_AGENT_JWT_ISSUER=https://identity.example.com/
PK_AGENT_JWT_AUDIENCE=production-knowledge-agent
PK_AGENT_RUNTIME_STORE=redis
```

若缺少 JWKS URL、issuer、audience 或 Redis runtime，启动将失败。JWKS 端点必须使用 HTTPS。接受的签名算法为 RS256 和 ES256；HS256 与开发令牌在生产边界会被拒绝。

## 密钥轮换

在使用新密钥签发令牌之前，先在新的 `kid` 下发布新的公钥。未知的 `kid` 会触发立即缓存刷新。保留旧公钥，直到所有用它签发的令牌都已过期，然后将其移除。在 `PK_AGENT_JWT_JWKS_CACHE_SECONDS` 缓存期内，复用 `kid` 关联的新密钥材料可能仍被缓存，因此不要复用密钥 ID。

## PostgreSQL RLS 检查

应用在启动时会为 `checkpoints`、`checkpoint_blobs` 和 `checkpoint_writes` 启用并强制 RLS。策略名称为 `pk_agent_<table>_tenant`。

```sql
SELECT tablename, policyname, qual, with_check
FROM pg_policies
WHERE policyname LIKE 'pk_agent_%_tenant';
```

每个 saver 操作仅针对当前事务设置 `pk_agent.tenant_id`。未设置该配置参数的查询应当返回零行。不要授予应用代码一条可禁用或绕过 RLS 的独立连接路径。

## 事故信号

- 反复出现未知 `kid` 的身份验证失败：在增加缓存有效期之前，先检查 IdP 轮换与 JWKS 可达性。
- `security_rejected` 任务事件：检查队列完整性与租户/线程构造；graph 未被调用。
- `prompt_injection_detected` 事件：检查引用的证据来源。该事件只包含计数，不包含可疑内容。
