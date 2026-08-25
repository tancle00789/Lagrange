from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PK_AGENT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/pk_agent"
    redis_url: str = "redis://localhost:6379/0"
    runtime_store: str = "memory"
    runtime_key_prefix: str = "pk_agent"
    task_retention_seconds: int = 604_800
    idempotency_ttl_seconds: int = 86_400
    worker_concurrency: int = 2
    worker_lease_ms: int = 30_000
    worker_block_ms: int = 1_000
    worker_max_attempts: int = 3
    worker_retry_backoff_seconds: float = 0.25
    log_level: str = "INFO"
    metrics_enabled: bool = True
    otlp_endpoint: str | None = None
    observability_service_name: str = "production-knowledge-agent"
    jwt_secret: str = "dev-only-secret"  # noqa: S105
    jwt_jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_jwks_cache_seconds: int = 300
    checkpoint_ns: str = "dev"
    model_budget_max_calls: int = 10
    rate_limit_rps: int = 20
    ssrf_allowlist: str = (
        "localhost,export.arxiv.org,arxiv.org,api.github.com,github.com,"
        "api.crossref.org,api.springernature.com,link.springer.com"
    )
    environment: str = "dev"
    sse_ticket_ttl_seconds: int = 60

    weknora_base_url: str = "http://weknora-app:8080"
    weknora_api_key: str | None = None
    weknora_timeout_seconds: float = 10.0
    weknora_max_retries: int = 3
    weknora_default_kb_id: str | None = None
    weknora_tenant_kb_map: str = ""
    weknora_snippet_max_chars: int = 1500
    retrieval_top_k: int = 10

    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str | None = None
    ollama_timeout_seconds: float = 120.0
    ollama_num_predict: int = 4096
    ollama_num_ctx: int = 16384

    arxiv_base_url: str = "https://export.arxiv.org"
    github_api_base_url: str = "https://api.github.com"
    github_token: str | None = None
    crossref_base_url: str = "https://api.crossref.org"
    crossref_mailto: str = ""
    springer_nature_base_url: str = "https://api.springernature.com"
    springer_nature_api_key: str | None = None
    external_http_timeout: float = 15.0
    external_max_retries: int = 2
    external_max_results: int = 5
    literature_abstract_max_chars: int = 2000
    literature_pdf_max_bytes: int = 25_000_000

    model_blocks_enabled: bool = True
    model_block_max_calls: int = 2
    model_block_timeout_seconds: int = 900
    model_run_result_ttl_seconds: int = 3600
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str = "pk-agent-artifacts"
    minio_secure: bool = False
    artifacts_local_dir: str = ".artifacts"

    @property
    def ssrf_allowlist_hosts(self) -> set[str]:
        return {item.strip() for item in self.ssrf_allowlist.split(",") if item.strip()}

    @property
    def weknora_kb_by_tenant(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for pair in self.weknora_tenant_kb_map.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            tenant, kb_id = pair.split(":", 1)
            mapping[tenant.strip()] = kb_id.strip()
        return mapping

    def validate_runtime_security(self) -> None:
        """Reject development credentials when the service is production-bound."""
        if self.environment.lower() != "production":
            return
        if not self.jwt_jwks_url or not self.jwt_issuer or not self.jwt_audience:
            raise ValueError(
                "PK_AGENT_JWT_JWKS_URL, PK_AGENT_JWT_ISSUER and PK_AGENT_JWT_AUDIENCE "
                "are required in production"
            )
        if not self.jwt_jwks_url.startswith("https://"):
            raise ValueError("PK_AGENT_JWT_JWKS_URL must use https in production")
        if self.runtime_store != "redis":
            raise ValueError("PK_AGENT_RUNTIME_STORE must be redis in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
