from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pk_agent.modelblocks.base import RunContext
from pk_agent.modelblocks.models import ModelBlockSpec, ModelRunResult


class KvCacheSimParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    num_layers: int = Field(default=32, ge=1, le=200)
    num_kv_heads: int = Field(default=8, ge=1, le=128)
    head_dim: int = Field(default=128, ge=16, le=512)
    context_length: int = Field(default=4096, ge=128, le=1_000_000)
    batch_size: int = Field(default=1, ge=1, le=64)
    bytes_per_element: int = Field(default=2, ge=1, le=8)
    # Cache compression ratios to evaluate. r >= 1.0 means the KV cache is
    # divided by r (r=1: no compression, r=4: quarter memory). Never < 1.0.
    compression_ratios: list[float] = Field(
        default_factory=lambda: [1.0, 2.0, 4.0, 8.0, 16.0]
    )
    # Heuristic quality-decay exponent: quality = 1 - (1 - 1/r)^gamma.
    quality_gamma: float = Field(default=0.5, gt=0.0, le=8.0)

    @field_validator("compression_ratios")
    @classmethod
    def _ratios_in_range(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("compression_ratios must not be empty")
        if any(not (1.0 <= r <= 1024.0) for r in value):
            raise ValueError("compression_ratios entries must be in [1.0, 1024.0]")
        return value


class KvCacheSimBlock:
    """Numerical KV-cache compression simulation (no heavy dependencies).

    For each compression ratio r, reports KV-cache memory (bytes and GiB)
    and a heuristic quality-retention proxy, so the user can reason about
    memory/quality trade-offs when studying KV-cache compression.
    """

    @property
    def spec(self) -> ModelBlockSpec:
        return ModelBlockSpec(
            name="kv_cache_sim",
            version="1.0.0",
            description=(
                "Simulate KV-cache memory usage and heuristic quality retention "
                "across compression ratios for a transformer config (layers, "
                "KV heads, head dim, context length, precision). "
                "compression_ratios must all be >= 1.0: ratio r means cache "
                "memory is divided by r (r=1 no compression, r=8 one-eighth "
                "memory). Pure numerical simulation; outputs a memory/quality "
                "trade-off curve."
            ),
            kind="sim",
            gpu_required=False,
            max_runtime_seconds=60,
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return KvCacheSimParams

    async def run(self, params: BaseModel, ctx: RunContext) -> ModelRunResult:
        assert isinstance(params, KvCacheSimParams)
        started = time.monotonic()
        try:
            if any(not (1.0 <= r <= 1024.0) for r in params.compression_ratios):
                raise ValueError("compression_ratios entries must be in [1.0, 1024.0]")
            curve, metrics = self._simulate(params)
        except Exception as exc:
            ctx.logger.exception("kv_cache_sim run %s failed", ctx.run_id)
            return ModelRunResult(
                run_id=ctx.run_id,
                block_name=self.spec.name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        curve_id = await ctx.artifacts.put(
            ctx.artifact_key("kv_cache_curve.json"),
            json.dumps(curve, indent=2).encode("utf-8"),
            "application/json",
        )
        return ModelRunResult(
            run_id=ctx.run_id,
            block_name=self.spec.name,
            status="completed",
            metrics=metrics,
            artifact_ids=[curve_id],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _simulate(
        self, params: KvCacheSimParams
    ) -> tuple[list[dict[str, float]], dict[str, float]]:
        # KV cache = 2 (K,V) * layers * kv_heads * head_dim * tokens * batch * bytes
        base_bytes = (
            2
            * params.num_layers
            * params.num_kv_heads
            * params.head_dim
            * params.context_length
            * params.batch_size
            * params.bytes_per_element
        )
        gib = 1024.0**3

        curve: list[dict[str, float]] = []
        for ratio in params.compression_ratios:
            memory_bytes = base_bytes / ratio
            quality = 1.0 - (1.0 - 1.0 / ratio) ** params.quality_gamma
            curve.append(
                {
                    "compression_ratio": ratio,
                    "memory_bytes": memory_bytes,
                    "memory_gib": memory_bytes / gib,
                    "quality_proxy": quality,
                    "effective_context_tokens": params.context_length / ratio,
                }
            )

        metrics: dict[str, float] = {
            "baseline_memory_gib": base_bytes / gib,
            "max_compression_ratio": max(params.compression_ratios),
            "min_memory_gib": min(p["memory_gib"] for p in curve),
            "min_quality_proxy": min(p["quality_proxy"] for p in curve),
        }
        return curve, metrics
