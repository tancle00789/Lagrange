# Identity and Tenant Isolation Operations

## Production identity configuration

Set all of the following:

```text
PK_AGENT_ENVIRONMENT=production
PK_AGENT_JWT_JWKS_URL=https://identity.example.com/.well-known/jwks.json
PK_AGENT_JWT_ISSUER=https://identity.example.com/
PK_AGENT_JWT_AUDIENCE=production-knowledge-agent
PK_AGENT_RUNTIME_STORE=redis
```

Startup fails if the JWKS URL, issuer, audience or Redis runtime is absent. The
JWKS endpoint must use HTTPS. Accepted signing algorithms are RS256 and ES256;
HS256 and development tokens are rejected at the production boundary.

## Key rotation

Publish the new public key under a new `kid` before issuing tokens with it. An
unknown `kid` forces an immediate cache refresh. Retain the old public key until
all tokens signed by it have expired, then remove it. Reusing a `kid` for new key
material can remain cached until `PK_AGENT_JWT_JWKS_CACHE_SECONDS`, so do not
reuse key IDs.

## PostgreSQL RLS checks

On startup the application enables and forces RLS on `checkpoints`,
`checkpoint_blobs` and `checkpoint_writes`. Policies are named
`pk_agent_<table>_tenant`.

```sql
SELECT tablename, policyname, qual, with_check
FROM pg_policies
WHERE policyname LIKE 'pk_agent_%_tenant';
```

Every saver operation sets `pk_agent.tenant_id` only for its current
transaction. Queries without that setting should return zero rows. Do not grant
application code a separate connection path that disables or bypasses RLS.

## Incident signals

- Repeated authentication failures with unknown `kid`: check IdP rotation and
  JWKS reachability before increasing the cache lifetime.
- `security_rejected` task event: inspect queue integrity and tenant/thread
  construction; the graph was not invoked.
- `prompt_injection_detected` event: inspect the cited evidence source. The
  event contains only a count, not the suspicious content.

