#!/usr/bin/env python3
"""Bootstrap a local WeKnora subsystem for pk_agent.

Waits for weknora-app, registers/logs in a dev user, ensures an API key,
an embedding model and a knowledge base exist, then prints the env vars
pk_agent needs. Idempotent: safe to re-run.

Secrets are only printed to stdout; nothing is written to disk or logs.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

BASE_URL = os.environ.get("WEKNORA_BASE_URL", "http://localhost:8081").rstrip("/")
API = f"{BASE_URL}/api/v1"

USERNAME = os.environ.get("WEKNORA_BOOTSTRAP_USER", "pk-agent-dev")
EMAIL = os.environ.get("WEKNORA_BOOTSTRAP_EMAIL", "pk-agent-dev@example.local")
PASSWORD = os.environ.get("WEKNORA_BOOTSTRAP_PASSWORD", "pk-agent-dev-password")

EMBEDDING_MODEL_NAME = os.environ.get("WEKNORA_EMBEDDING_MODEL", "bge-m3")
EMBEDDING_BASE_URL = os.environ.get(
    "WEKNORA_EMBEDDING_BASE_URL", "http://host.docker.internal:11434"
)
EMBEDDING_PROVIDER = os.environ.get("WEKNORA_EMBEDDING_PROVIDER", "ollama")
EMBEDDING_DIMENSION = int(os.environ.get("WEKNORA_EMBEDDING_DIMENSION", "1024"))

KB_NAME = os.environ.get("WEKNORA_BOOTSTRAP_KB_NAME", "pk-agent-default")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def wait_for_health(client: httpx.Client, timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = client.get(f"{BASE_URL}/health")
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    fail(f"weknora-app not healthy at {BASE_URL} within {timeout_seconds}s")


def unwrap(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.status_code >= 400:
        fail(f"{action} failed: {response.status_code} {response.text[:300]}")
    body = response.json()
    return body if isinstance(body, dict) else {"data": body}


def items_of(payload: dict[str, Any]) -> list[Any]:
    """List endpoints variously return a bare list, {"data": [...]}, or
    {"data": {"items": [...]}} — accept all three."""
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items
    return []


def main() -> None:
    with httpx.Client(timeout=15.0) as client:
        print(f"waiting for weknora-app at {BASE_URL} ...")
        wait_for_health(client)

        register = client.post(
            f"{API}/auth/register",
            json={"username": USERNAME, "email": EMAIL, "password": PASSWORD},
        )
        if register.status_code >= 400:
            print("register returned an error (user may already exist); continuing to login")

        login = unwrap(
            client.post(
                f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}
            ),
            "login",
        )
        token = login.get("token") or (login.get("data") or {}).get("token")
        if not token:
            fail(f"login response had no token: {str(login)[:300]}")
        auth = {"Authorization": f"Bearer {token}"}

        me = unwrap(client.get(f"{API}/auth/me", headers=auth), "auth/me")
        me_data = me.get("data") or me
        tenant_id = (
            (me_data.get("active_tenant") or {}).get("id")
            or (login.get("active_tenant") or {}).get("id")
            or me_data.get("tenant_id")
        )
        if not tenant_id:
            tenants = unwrap(
                client.post(f"{API}/tenants", json={"name": "pk-agent"}, headers=auth),
                "create tenant",
            )
            tenant_id = (tenants.get("data") or {}).get("id")
        if not tenant_id:
            fail("could not resolve tenant id")
        print(f"tenant id: {tenant_id}")

        key = unwrap(
            client.post(
                f"{API}/tenants/{tenant_id}/api-keys",
                json={"name": "pk-agent", "full_access": True},
                headers=auth,
            ),
            "create api key",
        )
        api_token = (key.get("data") or {}).get("token")
        if not api_token:
            fail("api key creation returned no token")
        print("api key created (name=pk-agent)")

        models = unwrap(client.get(f"{API}/models", headers=auth), "list models")
        model_items = items_of(models)
        embedding_model_id = next(
            (
                m.get("id")
                for m in model_items
                if isinstance(m, dict) and m.get("type") == "Embedding"
            ),
            None,
        )
        if not embedding_model_id:
            created = unwrap(
                client.post(
                    f"{API}/models",
                    json={
                        "name": EMBEDDING_MODEL_NAME,
                        "type": "Embedding",
                        "source": "local",
                        "is_default": True,
                        "parameters": {
                            "base_url": EMBEDDING_BASE_URL,
                            "provider": EMBEDDING_PROVIDER,
                            "embedding_parameters": {"dimension": EMBEDDING_DIMENSION},
                        },
                    },
                    headers=auth,
                ),
                "create embedding model",
            )
            embedding_model_id = (created.get("data") or {}).get("id")
        if not embedding_model_id:
            fail("could not resolve embedding model id")
        print(f"embedding model id: {embedding_model_id}")

        kbs = unwrap(client.get(f"{API}/knowledge-bases", headers=auth), "list KBs")
        kb_items = items_of(kbs)
        kb_id = next(
            (
                k.get("id")
                for k in kb_items
                if isinstance(k, dict) and k.get("name") == KB_NAME
            ),
            None,
        )
        if not kb_id:
            created_kb = unwrap(
                client.post(
                    f"{API}/knowledge-bases",
                    json={
                        "name": KB_NAME,
                        "type": "document",
                        "embedding_model_id": embedding_model_id,
                        "chunking_config": {
                            "chunk_size": 512,
                            "chunk_overlap": 64,
                            "separators": ["\n\n", "\n", "。"],
                        },
                    },
                    headers=auth,
                ),
                "create knowledge base",
            )
            kb_id = (created_kb.get("data") or {}).get("id")
        if not kb_id:
            fail("could not resolve knowledge base id")
        print(f"knowledge base id: {kb_id}")

    print("\nExport these for pk_agent (compose reads them from the shell env):")
    print(f'export PK_AGENT_WEKNORA_API_KEY="{api_token}"')
    print(f'export PK_AGENT_WEKNORA_DEFAULT_KB_ID="{kb_id}"')


if __name__ == "__main__":
    main()
