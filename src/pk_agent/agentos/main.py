from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from starlette.middleware.base import RequestResponseEndpoint

from pk_agent.agentos.api.v1.literature import router as literature_router
from pk_agent.agentos.api.v1.tasks import execute_job
from pk_agent.agentos.api.v1.tasks import router as tasks_router
from pk_agent.agentos.cancellation import (
    CancellationCoordinator,
    RedisCancellationCoordinator,
)
from pk_agent.agentos.dependencies import RateLimit, RateLimiter, RedisRateLimiter
from pk_agent.agentos.job_queue import RedisJobQueue
from pk_agent.agentos.task_store import InMemoryTaskStore, RedisTaskStore, TaskStore
from pk_agent.config import Settings, get_settings
from pk_agent.graph.checkpointer import open_checkpointer
from pk_agent.graph.research_graph import build_research_graph
from pk_agent.harness.bus import AgentEventBus, EventBus, RedisAgentEventBus
from pk_agent.harness.ollama import OllamaModel
from pk_agent.harness.registry import ToolRegistry, ToolRisk, ToolSpec
from pk_agent.harness.router import FakeModel, Model, ModelRouter
from pk_agent.literature.arxiv import ArxivSource
from pk_agent.literature.base import LiteratureSource, SourceHttpClient
from pk_agent.literature.crossref import CrossRefSource
from pk_agent.literature.github import GitHubSource
from pk_agent.literature.nature import NatureSource
from pk_agent.literature.tools import (
    make_literature_ingest_handler,
    make_literature_search_handler,
)
from pk_agent.observability import Observability, configure_json_logging
from pk_agent.providers.base import KnowledgeProvider
from pk_agent.providers.fake import FakeKnowledgeProvider
from pk_agent.providers.weknora import WeKnoraKnowledgeProvider
from pk_agent.providers.weknora_client import WeKnoraClient
from pk_agent.security.jwks import JwksAuthenticator
from pk_agent.security.sse import RedisSseTicketStore, SseTicketStore, TicketStore

STATIC_DIR_CANDIDATES = [
    Path("/app/static"),
    Path(__file__).resolve().parents[3] / "frontend" / "dist",
]
DEV_CORS_ORIGINS = ["http://localhost:5173"]


def _build_task_store(settings: Settings) -> TaskStore:
    if settings.runtime_store == "memory":
        return InMemoryTaskStore()
    if settings.runtime_store != "redis":
        raise ValueError(f"unsupported runtime store: {settings.runtime_store}")
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisTaskStore(
        client,
        key_prefix=settings.runtime_key_prefix,
        task_retention_seconds=settings.task_retention_seconds,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
    )


def _build_knowledge_provider(settings: Settings) -> KnowledgeProvider:
    if settings.weknora_api_key:
        client = WeKnoraClient(
            settings.weknora_base_url,
            settings.weknora_api_key,
            timeout_seconds=settings.weknora_timeout_seconds,
            max_retries=settings.weknora_max_retries,
        )
        return WeKnoraKnowledgeProvider(
            client,
            default_kb_id=settings.weknora_default_kb_id,
            kb_by_tenant=settings.weknora_kb_by_tenant,
            snippet_max_chars=settings.weknora_snippet_max_chars,
        )
    return FakeKnowledgeProvider(snippet_max_chars=settings.weknora_snippet_max_chars)


def _build_answer_model(settings: Settings) -> Model:
    if settings.ollama_chat_model:
        return OllamaModel(
            settings.ollama_base_url,
            settings.ollama_chat_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
    return FakeModel()


def _build_literature_sources(settings: Settings) -> dict[str, LiteratureSource]:
    def _http() -> SourceHttpClient:
        return SourceHttpClient(
            timeout_seconds=settings.external_http_timeout,
            max_retries=settings.external_max_retries,
        )

    sources: dict[str, LiteratureSource] = {
        "arxiv": ArxivSource(
            base_url=settings.arxiv_base_url,
            abstract_max_chars=settings.literature_abstract_max_chars,
            http=_http(),
        ),
        "github": GitHubSource(
            base_url=settings.github_api_base_url,
            token=settings.github_token,
            abstract_max_chars=settings.literature_abstract_max_chars,
            http=_http(),
        ),
        "crossref": CrossRefSource(
            base_url=settings.crossref_base_url,
            mailto=settings.crossref_mailto,
            abstract_max_chars=settings.literature_abstract_max_chars,
            http=_http(),
        ),
    }
    if settings.springer_nature_api_key:
        sources["nature"] = NatureSource(
            api_key=settings.springer_nature_api_key,
            base_url=settings.springer_nature_base_url,
            abstract_max_chars=settings.literature_abstract_max_chars,
            http=_http(),
        )
    return sources


def _build_tool_registry(
    settings: Settings,
    knowledge_provider: KnowledgeProvider,
    sources: dict[str, LiteratureSource],
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="literature_search",
            version="1.0",
            description="Search external literature sources (arxiv/github/crossref/nature)",
            risk=ToolRisk.L1,
            handler=make_literature_search_handler(sources),
            timeout_seconds=settings.external_http_timeout * 2,
            max_retries=0,
        )
    )
    registry.register(
        ToolSpec(
            name="literature_ingest",
            version="1.0",
            description="Download a literature PDF and ingest it into the knowledge base",
            risk=ToolRisk.L2,
            handler=make_literature_ingest_handler(
                provider=knowledge_provider,
                allowlist=settings.ssrf_allowlist_hosts,
                max_bytes=settings.literature_pdf_max_bytes,
            ),
            timeout_seconds=60.0,
            max_retries=0,
        )
    )
    return registry


def create_app(
    settings: Settings | None = None,
    *,
    literature_sources: dict[str, LiteratureSource] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_runtime_security()
    configure_json_logging(settings.log_level)
    observability = Observability(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        checkpointer, close_checkpointer = await open_checkpointer(settings.database_url)
        jwks_authenticator = (
            JwksAuthenticator(
                settings.jwt_jwks_url,
                issuer=settings.jwt_issuer or "",
                audience=settings.jwt_audience or "",
                cache_seconds=settings.jwt_jwks_cache_seconds,
            )
            if settings.jwt_jwks_url
            else None
        )
        task_store = _build_task_store(settings)
        event_bus: EventBus
        rate_limiter: RateLimit
        sse_tickets: TicketStore
        cancellation: CancellationCoordinator
        if isinstance(task_store, RedisTaskStore):
            await task_store.client.ping()
            runtime_redis = task_store.client
            event_bus = RedisAgentEventBus(runtime_redis, key_prefix=settings.runtime_key_prefix)
            rate_limiter = RedisRateLimiter(
                runtime_redis,
                settings.rate_limit_rps,
                key_prefix=settings.runtime_key_prefix,
            )
            sse_tickets = RedisSseTicketStore(
                runtime_redis,
                ttl_seconds=settings.sse_ticket_ttl_seconds,
                key_prefix=settings.runtime_key_prefix,
            )
            cancellation = RedisCancellationCoordinator(
                runtime_redis, key_prefix=settings.runtime_key_prefix
            )
            job_queue = RedisJobQueue(
                runtime_redis,
                key_prefix=settings.runtime_key_prefix,
                concurrency=settings.worker_concurrency,
                lease_ms=settings.worker_lease_ms,
                block_ms=settings.worker_block_ms,
                retry_backoff_seconds=settings.worker_retry_backoff_seconds,
            )
        else:
            event_bus = AgentEventBus()
            rate_limiter = RateLimiter(settings.rate_limit_rps)
            sse_tickets = SseTicketStore(settings.sse_ticket_ttl_seconds)
            cancellation = CancellationCoordinator()
            job_queue = None
        model_router = ModelRouter()
        model_router.register("answer", _build_answer_model(settings))
        knowledge_provider = _build_knowledge_provider(settings)
        sources = (
            literature_sources
            if literature_sources is not None
            else _build_literature_sources(settings)
        )
        tool_registry = _build_tool_registry(settings, knowledge_provider, sources)

        app.state.settings = settings
        app.state.observability = observability
        app.state.jwks_authenticator = jwks_authenticator
        app.state.event_bus = event_bus
        app.state.model_router = model_router
        app.state.knowledge_provider = knowledge_provider
        app.state.literature_sources = sources
        app.state.tool_registry = tool_registry
        app.state.sse_tickets = sse_tickets
        app.state.rate_limiter = rate_limiter
        app.state.cancellation = cancellation
        app.state.task_store = task_store
        app.state.job_queue = job_queue
        app.state.task_runs = {}
        app.state.shutting_down = False

        def cancel_owned(task_id: str) -> None:
            run = app.state.task_runs.get(task_id)
            if run is not None and not run.done():
                run.cancel()

        if isinstance(cancellation, RedisCancellationCoordinator):
            await cancellation.start(cancel_owned)
        app.state.graph = build_research_graph(
            checkpointer=checkpointer,
            model_router=model_router,
            event_bus=event_bus,
            max_budget_calls=settings.model_budget_max_calls,
            knowledge_provider=knowledge_provider,
            tool_registry=tool_registry,
        )
        if job_queue is not None:
            await job_queue.start(lambda job: execute_job(app, job))
        yield
        app.state.shutting_down = True
        await cancellation.aclose()
        if job_queue is not None:
            await job_queue.aclose()
        active_runs = list(app.state.task_runs.values())
        for run in active_runs:
            run.cancel()
        if active_runs:
            await asyncio.gather(*active_runs, return_exceptions=True)
        for source in sources.values():
            http = getattr(source, "http", None)
            if http is not None:
                await http.aclose()
        provider_client = getattr(knowledge_provider, "client", None)
        if provider_client is not None:
            await provider_client.aclose()
        await task_store.aclose()
        if jwks_authenticator is not None:
            await jwks_authenticator.aclose()
        await close_checkpointer()
        observability.shutdown()

    app = FastAPI(title="Production Knowledge Agent", lifespan=lifespan)

    @app.middleware("http")
    async def observe_request(request: Request, call_next: RequestResponseEndpoint):
        return await observability.http_middleware(request, call_next)

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return observability.metrics_response()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tasks_router, prefix="/api/v1/tasks", tags=["tasks"])
    app.include_router(literature_router, prefix="/api/v1/literature", tags=["literature"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    static_dir = next((path for path in STATIC_DIR_CANDIDATES if path.is_dir()), None)
    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


app = create_app()
