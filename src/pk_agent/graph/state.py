from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ResearchState(TypedDict, total=False):
    task_id: str
    tenant_id: str
    status: str
    input: str
    require_approval: bool
    approved: bool
    normalized_query: str
    result: str
    report: dict[str, Any]
    budget_calls: int
    knowledge_hits: list[dict[str, Any]]
    knowledge_error: str
    allow_external: bool
    ingest_external: bool
    external_sources: list[dict[str, Any]]
    external_error: str
    retrieval_rounds: int
    messages: Annotated[list[dict[str, Any]], operator.add]
