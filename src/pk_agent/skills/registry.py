from __future__ import annotations

import re

from pk_agent.skills.models import SkillRecord, TriggerCondition
from pk_agent.skills.store import SkillStore


class SkillRegistry:
    """Runtime view over approved skills.

    Explicitly loaded from the store — there is no implicit registration path.
    Matching is deterministic (keyword / regex), never LLM-based, so it costs
    no model budget and is fully testable.
    """

    def __init__(self, store: SkillStore, *, max_matched: int = 3) -> None:
        self._store = store
        self._max_matched = max_matched
        self._approved: dict[str, SkillRecord] = {}

    async def load_approved(self) -> None:
        records = await self._store.list_approved_latest()
        self._approved = {record.skill_id: record for record in records}

    async def refresh(self) -> None:
        """Reload after an approval/deprecation (single-process freshness)."""
        await self.load_approved()

    def get(self, skill_id: str) -> SkillRecord | None:
        return self._approved.get(skill_id)

    def approved(self) -> list[SkillRecord]:
        return sorted(self._approved.values(), key=lambda r: r.skill_id)

    def match(self, query: str) -> list[SkillRecord]:
        scored: list[tuple[int, SkillRecord]] = []
        for record in self._approved.values():
            priority = self._match_priority(query, record.trigger_conditions)
            if priority is not None:
                scored.append((priority, record))
        scored.sort(key=lambda item: (-item[0], item[1].skill_id))
        return [record for _, record in scored[: self._max_matched]]

    @staticmethod
    def _match_priority(
        query: str, conditions: list[TriggerCondition]
    ) -> int | None:
        lowered = query.lower()
        best: int | None = None
        for condition in conditions:
            hit = any(
                keyword.lower() in lowered for keyword in condition.keywords
            ) or any(
                re.search(pattern, query, re.IGNORECASE)
                for pattern in condition.regex_patterns
            )
            if hit and (best is None or condition.priority > best):
                best = condition.priority
        return best
