from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PK_AGENT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/pk_agent"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "dev-only-secret"  # noqa: S105
    checkpoint_ns: str = "dev"
    model_budget_max_calls: int = 10
    rate_limit_rps: int = 20
    ssrf_allowlist: str = (
        "localhost,export.arxiv.org,arxiv.org,api.github.com,github.com,"
        "api.crossref.org,api.springernature.com,link.springer.com"
    )
    environment: str = "dev"

    weknora_base_url: str = "http://weknora-app:8080"
    weknora_api_key: str | None = None
    weknora_timeout_seconds: float = 10.0
    weknora_max_retries: int = 3
    weknora_default_kb_id: str | None = None
    weknora_tenant_kb_map: str = ""
    weknora_snippet_max_chars: int = 500

    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str | None = None
    ollama_timeout_seconds: float = 60.0

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
    literature_abstract_max_chars: int = 800
    literature_pdf_max_bytes: int = 25_000_000

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
