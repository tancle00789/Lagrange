from __future__ import annotations

import time

AUTH = {"Authorization": "Bearer dev:tenant-a:user-1:admin"}
OTHER_AUTH = {"Authorization": "Bearer dev:tenant-b:user-2:admin"}
VIEWER_AUTH = {"Authorization": "Bearer dev:tenant-a:user-3:viewer"}


def _wait_for_completion(client, task_id: str) -> dict:
    for _ in range(50):
        response = client.get(f"/api/v1/tasks/{task_id}", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed", "paused"}:
            return body
        time.sleep(0.05)
    raise AssertionError("task did not reach terminal state")


def test_create_and_complete_task(client) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={**AUTH, "Idempotency-Key": "it-1"},
        json={"input": "hello agent", "require_approval": False},
    )
    assert created.status_code == 202
    task_id = created.json()["task_id"]

    final = _wait_for_completion(client, task_id)
    assert final["status"] == "completed"
    assert final["result"].startswith("FAKE[answer]:")
    assert len(final["knowledge_hits"]) == 2
    assert final["knowledge_hits"][0]["source"]["kb_id"] == "fake-kb"

    report = final["report"]
    assert report is not None
    assert report["grounded"] is True
    known_ids = {hit["chunk_id"] for hit in final["knowledge_hits"]}
    assert report["claims"]
    for claim in report["claims"]:
        assert set(claim["chunk_ids"]) <= known_ids

    replay = client.post(
        "/api/v1/tasks",
        headers={**AUTH, "Idempotency-Key": "it-1"},
        json={"input": "hello agent", "require_approval": False},
    )
    assert replay.status_code == 202
    assert replay.json()["task_id"] == task_id


def test_requires_auth(client) -> None:
    response = client.post("/api/v1/tasks", json={"input": "x"})
    assert response.status_code == 401


def test_create_requires_admin_role(client) -> None:
    response = client.post("/api/v1/tasks", headers=VIEWER_AUTH, json={"input": "x"})
    assert response.status_code == 403


def test_tenant_isolation(client) -> None:
    created = client.post("/api/v1/tasks", headers=AUTH, json={"input": "secret"})
    task_id = created.json()["task_id"]

    response = client.get(f"/api/v1/tasks/{task_id}", headers=OTHER_AUTH)
    assert response.status_code == 404


def test_idempotency_key_is_tenant_scoped(client) -> None:
    first = client.post(
        "/api/v1/tasks",
        headers={**AUTH, "Idempotency-Key": "shared-key"},
        json={"input": "tenant a"},
    )
    second = client.post(
        "/api/v1/tasks",
        headers={**OTHER_AUTH, "Idempotency-Key": "shared-key"},
        json={"input": "tenant b"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] != second.json()["task_id"]


def test_sse_ticket_is_task_bound_and_single_use(client) -> None:
    created = client.post("/api/v1/tasks", headers=AUTH, json={"input": "events"})
    task_id = created.json()["task_id"]

    issued = client.post(f"/api/v1/tasks/{task_id}/events/ticket", headers=AUTH)
    assert issued.status_code == 200
    body = issued.json()
    assert body["expires_in"] == 60

    ticket = client.portal.call(client.app.state.sse_tickets.consume, body["ticket"], task_id)
    assert ticket is not None
    assert ticket.tenant_id == "tenant-a"
    assert client.portal.call(client.app.state.sse_tickets.consume, body["ticket"], task_id) is None

    hidden = client.post(f"/api/v1/tasks/{task_id}/events/ticket", headers=OTHER_AUTH)
    assert hidden.status_code == 404


def test_list_tasks_scoped_to_tenant(client) -> None:
    created = client.post("/api/v1/tasks", headers=AUTH, json={"input": "list me"})
    task_id = created.json()["task_id"]

    listed = client.get("/api/v1/tasks", headers=AUTH)
    assert listed.status_code == 200
    ids = [item["task_id"] for item in listed.json()]
    assert task_id in ids

    other = client.get("/api/v1/tasks", headers=OTHER_AUTH)
    assert other.status_code == 200
    assert task_id not in [item["task_id"] for item in other.json()]


def test_require_approval_pauses_then_resume(client) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=AUTH,
        json={"input": "needs approval", "require_approval": True},
    )
    task_id = created.json()["task_id"]

    final = _wait_for_completion(client, task_id)
    assert final["status"] == "paused"
    assert final["report"] is not None

    resumed = client.post(
        f"/api/v1/tasks/{task_id}/resume",
        headers=AUTH,
        json={"approved": True, "note": "looks good"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"


def test_literature_search_endpoint(literature_client) -> None:
    response = literature_client.post(
        "/api/v1/literature/search",
        headers=AUTH,
        json={"query": "kv cache", "limit": 3},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert len(body["records"]) == 1
    assert body["records"][0]["source_type"] == "arxiv"
    assert body["records"][0]["external_id"] == "2401.00001"

    unauthorized = literature_client.post("/api/v1/literature/search", json={"query": "kv cache"})
    assert unauthorized.status_code == 401

    unknown_source = literature_client.post(
        "/api/v1/literature/search",
        headers=AUTH,
        json={"query": "kv cache", "sources": ["nope"]},
    )
    assert unknown_source.status_code == 502


def test_task_with_external_sources(literature_client) -> None:
    created = literature_client.post(
        "/api/v1/tasks",
        headers=AUTH,
        json={"input": "obscure topic with no internal docs", "allow_external": True},
    )
    assert created.status_code == 202
    task_id = created.json()["task_id"]

    final = _wait_for_completion(literature_client, task_id)
    assert final["status"] == "completed"
    # FakeKnowledgeProvider returns 2 hits (< threshold of 3), so the
    # external branch runs and the fake arxiv record lands in state.
    assert len(final["external_sources"]) == 1
    record = final["external_sources"][0]
    assert record["source_type"] == "arxiv"
    source_id = f"{record['source_type']}:{record['external_id']}"
    known_ids = {hit["chunk_id"] for hit in final["knowledge_hits"]} | {source_id}
    for claim in final["report"]["claims"]:
        assert set(claim["chunk_ids"]) <= known_ids
