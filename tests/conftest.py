from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pk_agent.agentos.main import create_app
from pk_agent.config import Settings
from pk_agent.literature.fake import FakeLiteratureSource


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="memory://",
        rate_limit_rps=1000,
        model_budget_max_calls=3,
        checkpoint_ns="test",
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def literature_client(settings: Settings):
    sources = {"arxiv": FakeLiteratureSource()}
    app = create_app(settings, literature_sources=sources)
    with TestClient(app) as test_client:
        yield test_client
