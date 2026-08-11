from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pk_agent.agentos.main import create_app
from pk_agent.config import Settings

AUTH = {"Authorization": "Bearer dev:tenant-a:user-1:admin"}
OTHER_AUTH = {"Authorization": "Bearer dev:tenant-b:user-2:admin"}


@pytest.fixture
def modelblocks_client(settings: Settings, tmp_path):
    settings.artifacts_local_dir = str(tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_modelblocks_catalog(modelblocks_client) -> None:
    response = modelblocks_client.get("/api/v1/modelblocks", headers=AUTH)
    assert response.status_code == 200
    catalog = {entry["name"]: entry for entry in response.json()}
    assert {"sklearn_tabular", "torch_mlp", "kv_cache_sim"} <= set(catalog)
    assert catalog["kv_cache_sim"]["kind"] == "sim"
    assert "properties" in catalog["kv_cache_sim"]["params_schema"]


def test_modelblocks_catalog_requires_auth(modelblocks_client) -> None:
    response = modelblocks_client.get("/api/v1/modelblocks")
    assert response.status_code == 401


def test_artifact_download_roundtrip(modelblocks_client) -> None:
    store = modelblocks_client.app.state.artifact_store
    import asyncio

    artifact_id = asyncio.run(
        store.put("tenant-a/task-1/run-1-curve.json", b'{"ok": true}', "application/json")
    )
    response = modelblocks_client.get(f"/api/v1/artifacts/{artifact_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.content == b'{"ok": true}'
    assert response.headers["content-type"].startswith("application/json")


def test_artifact_cross_tenant_denied(modelblocks_client) -> None:
    store = modelblocks_client.app.state.artifact_store
    import asyncio

    artifact_id = asyncio.run(
        store.put("tenant-a/task-1/run-1-model.joblib", b"model-bytes", "application/octet-stream")
    )
    response = modelblocks_client.get(f"/api/v1/artifacts/{artifact_id}", headers=OTHER_AUTH)
    assert response.status_code == 404


def test_artifact_traversal_and_missing(modelblocks_client) -> None:
    response = modelblocks_client.get(
        "/api/v1/artifacts/tenant-a/..%2F..%2Fevil", headers=AUTH
    )
    assert response.status_code == 404
    response = modelblocks_client.get(
        "/api/v1/artifacts/tenant-a/task-1/nope.json", headers=AUTH
    )
    assert response.status_code == 404


def test_task_response_includes_model_runs(client) -> None:
    created = client.post("/api/v1/tasks", headers=AUTH, json={"input": "simulate"})
    task_id = created.json()["task_id"]
    import time

    for _ in range(50):
        body = client.get(f"/api/v1/tasks/{task_id}", headers=AUTH).json()
        if body["status"] in {"completed", "failed", "paused"}:
            break
        time.sleep(0.05)
    assert "model_runs" in body
    # FakeModel's plan output is unparseable -> no runs, but the field exists.
    assert body["model_runs"] == []
