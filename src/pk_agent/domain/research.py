from __future__ import annotations

from pydantic import BaseModel, Field

from pk_agent.providers.contract import SourceReference


class Evidence(BaseModel):
    chunk_id: str
    source: SourceReference
    snippet: str = ""
    score: float = 0.0


class Claim(BaseModel):
    text: str
    chunk_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    grounded: bool = False
    dropped_citations: int = 0
    notes: list[str] = Field(default_factory=list)
