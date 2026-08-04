from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class Model(Protocol):
    async def generate(self, prompt: str, *, capability: str) -> str: ...


class FakeModel:
    def __init__(self, response: str | None = None) -> None:
        self.response = response

    async def generate(self, prompt: str, *, capability: str) -> str:
        if self.response is not None:
            return self.response
        return f"FAKE[{capability}]: {prompt}"


@dataclass
class ModelRouter:
    _models: dict[str, Model] = field(default_factory=dict)

    def register(self, capability: str, model: Model) -> None:
        self._models[capability] = model

    async def generate(self, prompt: str, *, capability: str) -> str:
        model = self._models.get(capability)
        if model is None:
            raise KeyError(f"no model registered for capability: {capability}")
        return await model.generate(prompt, capability=capability)
