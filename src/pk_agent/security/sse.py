from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from pk_agent.security.tenant import Principal


@dataclass(frozen=True)
class SseTicket:
    tenant_id: str
    user_id: str
    task_id: str
    expires_at: float


class TicketStore(Protocol):
    ttl_seconds: int

    async def issue(self, principal: Principal, task_id: str) -> str: ...

    async def consume(self, token: str, task_id: str) -> SseTicket | None: ...


class SseTicketStore:
    """Process-local, short-lived, single-use credentials for EventSource."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        if ttl_seconds < 1:
            raise ValueError("SSE ticket TTL must be positive")
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[str, SseTicket] = {}

    async def issue(self, principal: Principal, task_id: str) -> str:
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        self._tickets[token] = SseTicket(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            task_id=task_id,
            expires_at=time.monotonic() + self.ttl_seconds,
        )
        return token

    async def consume(self, token: str, task_id: str) -> SseTicket | None:
        ticket = self._tickets.pop(token, None)
        if ticket is None or ticket.task_id != task_id or ticket.expires_at <= time.monotonic():
            return None
        return ticket

    def _purge_expired(self) -> None:
        now = time.monotonic()
        self._tickets = {
            token: ticket for token, ticket in self._tickets.items() if ticket.expires_at > now
        }


class RedisSseTicketStore:
    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int = 60,
        key_prefix: str = "pk_agent",
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("SSE ticket TTL must be positive")
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix

    def _key(self, token: str) -> str:
        return f"{self.key_prefix}:sse-ticket:{token}"

    async def issue(self, principal: Principal, task_id: str) -> str:
        token = secrets.token_urlsafe(32)
        payload = {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "task_id": task_id,
        }
        await self.client.set(
            self._key(token),
            json.dumps(payload, separators=(",", ":")),
            ex=self.ttl_seconds,
        )
        return token

    async def consume(self, token: str, task_id: str) -> SseTicket | None:
        raw = await self.client.getdel(self._key(token))
        if raw is None:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("task_id") != task_id:
            return None
        return SseTicket(
            tenant_id=str(payload.get("tenant_id", "")),
            user_id=str(payload.get("user_id", "")),
            task_id=task_id,
            expires_at=time.monotonic() + self.ttl_seconds,
        )
