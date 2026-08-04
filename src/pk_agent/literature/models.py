from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SourceType = Literal["arxiv", "github", "crossref", "nature"]


class LiteratureRecord(BaseModel):
    """Metadata-only record for an external literature source.

    Full text / PDFs never enter graph state or this model; only metadata and
    a truncated abstract are carried (see ADR 006).
    """

    source_type: SourceType
    external_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    url: str = ""
    pdf_url: str | None = None
    doi: str | None = None
    venue: str = ""
    published: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return f"{self.source_type}:{self.external_id}"
