from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pk_agent.agentos.api.v1.literature import router as literature_router
from pk_agent.agentos.api.v1.tasks import router as tasks_router
from pk_agent.config import Settings, get_settings
from pk_agent.graph.checkpointer import open_checkpointer
from pk_agent.graph.research_graph import build_research_graph
from pk_agent.harness.bus import AgentEventBus
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
from pk_agent.providers.base import KnowledgeProvider
from pk_agent.providers.fake import FakeKnowledgeProvider
from pk_agent.providers.weknora import WeKnoraKnowledgeProvider
from pk_agent.providers.weknora_client import WeKnoraClient

STATIC_DIR_CANDIDATES = [
    Path("/app/static"),
    Path(__file__).resolve().parents[3] / "frontend" / "dist",
]
DEV_CORS_ORIGINS = ["http://localhost:5173"]


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        checkpointer, close_checkpointer = await open_checkpointer(settings.database_url)
        event_bus = AgentEventBus()
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
        app.state.event_bus = event_bus
        app.state.model_router = model_router
        app.state.knowledge_provider = knowledge_provider
        app.state.literature_sources = sources
        app.state.tool_registry = tool_registry
        app.state.graph = build_research_graph(
            checkpointer=checkpointer,
            model_router=model_router,
            event_bus=event_bus,
            max_budget_calls=settings.model_budget_max_calls,
            knowledge_provider=knowledge_provider,
            tool_registry=tool_registry,
        )
        app.state.tasks = {}
        app.state.idempotency = {}
        yield
        for source in sources.values():
            http = getattr(source, "http", None)
            if http is not None:
                await http.aclose()
        provider_client = getattr(knowledge_provider, "client", None)
        if provider_client is not None:
            await provider_client.aclose()
        await close_checkpointer()

    app = FastAPI(title="Production Knowledge Agent", lifespan=lifespan)
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
