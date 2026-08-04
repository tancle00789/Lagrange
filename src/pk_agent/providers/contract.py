from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    kb_id: str
    document_id: str
    title: str = ""
    chunk_index: int = 0


class KnowledgeHit(BaseModel):
    chunk_id: str
    score: float = 0.0
    source: SourceReference
    snippet: str = ""


class KnowledgeChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeIngestionJob(BaseModel):
    job_id: str
    status: str
    error: str | None = None


class CitationLocation(BaseModel):
    chunk_id: str
    document_id: str
    kb_id: str
    chunk_index: int = 0
    start_at: int | None = None
    end_at: int | None = None
