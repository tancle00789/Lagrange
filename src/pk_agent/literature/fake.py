from __future__ import annotations

from pk_agent.literature.models import LiteratureRecord


class FakeLiteratureSource:
    """Deterministic in-memory source for unit/integration tests."""

    def __init__(
        self,
        records: list[LiteratureRecord] | None = None,
        *,
        source_type: str = "arxiv",
    ) -> None:
        self.source_type = source_type
        self.records = records or [
            LiteratureRecord(
                source_type="arxiv",
                external_id="2401.00001",
                title="Fake Paper",
                authors=["A. Researcher"],
                abstract="A deterministic abstract for tests.",
                url="https://arxiv.org/abs/2401.00001",
                published="2024-01-01",
            )
        ]

    async def search(self, query: str, *, limit: int = 5) -> list[LiteratureRecord]:
        return self.records[: max(1, limit)]

    async def aclose(self) -> None:
        return None
