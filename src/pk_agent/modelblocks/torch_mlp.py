from __future__ import annotations

import asyncio
import io
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pk_agent.modelblocks.base import RunContext
from pk_agent.modelblocks.models import ModelBlockSpec, ModelRunResult

MAX_ROWS = 4000
MAX_FEATURES = 64
CUDA_MEMORY_FRACTION = 0.5


class TorchMlpParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: Literal["classification", "regression"] = "classification"
    hidden_layers: list[int] = Field(default_factory=lambda: [64, 32])
    epochs: int = Field(default=20, ge=1, le=200)
    learning_rate: float = Field(default=1e-3, gt=0.0, le=1.0)
    batch_size: int = Field(default=64, ge=8, le=1024)
    # Rows of floats; the last column is the target. None -> synthetic demo data.
    data: list[list[float]] | None = None
    demo_samples: int = Field(default=1000, ge=64, le=MAX_ROWS)
    demo_features: int = Field(default=10, ge=2, le=MAX_FEATURES)
    random_state: int = 0

    @model_validator(mode="after")
    def _check(self) -> TorchMlpParams:
        if not (1 <= len(self.hidden_layers) <= 8):
            raise ValueError("hidden_layers must have 1..8 entries")
        if any(not (4 <= width <= 1024) for width in self.hidden_layers):
            raise ValueError("hidden layer widths must be in 4..1024")
        if self.data is not None:
            if not (8 <= len(self.data) <= MAX_ROWS):
                raise ValueError(f"data must have 8..{MAX_ROWS} rows")
            width = len(self.data[0])
            if not (2 <= width <= MAX_FEATURES + 1):
                raise ValueError(
                    f"rows must have 2..{MAX_FEATURES + 1} columns (features + target)"
                )
            if any(len(row) != width for row in self.data):
                raise ValueError("all rows must have the same number of columns")
        return self


class TorchMlpBlock:
    @property
    def spec(self) -> ModelBlockSpec:
        return ModelBlockSpec(
            name="torch_mlp",
            version="1.0.0",
            description=(
                "Train a configurable multi-layer perceptron (PyTorch) on tabular "
                "data or a synthetic demo dataset. Uses CUDA when available "
                "(memory capped at 50%). Returns loss curve, final metrics and a "
                "weights artifact."
            ),
            kind="torch",
            gpu_required=False,
            max_runtime_seconds=900,
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return TorchMlpParams

    async def run(self, params: BaseModel, ctx: RunContext) -> ModelRunResult:
        assert isinstance(params, TorchMlpParams)
        started = time.monotonic()
        try:
            metrics, loss_curve, weights = await asyncio.to_thread(
                self._train, params, ctx
            )
        except Exception as exc:
            ctx.logger.exception("torch_mlp run %s failed", ctx.run_id)
            return ModelRunResult(
                run_id=ctx.run_id,
                block_name=self.spec.name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        curve_id = await ctx.artifacts.put(
            ctx.artifact_key("loss_curve.json"),
            json.dumps(loss_curve).encode("utf-8"),
            "application/json",
        )
        weights_id = await ctx.artifacts.put(
            ctx.artifact_key("weights.pt"), weights, "application/octet-stream"
        )
        return ModelRunResult(
            run_id=ctx.run_id,
            block_name=self.spec.name,
            status="completed",
            metrics=metrics,
            artifact_ids=[curve_id, weights_id],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _train(
        self, params: TorchMlpParams, ctx: RunContext
    ) -> tuple[dict[str, float], list[float], bytes]:
        import numpy as np
        import torch
        from torch import nn

        torch.manual_seed(params.random_state)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            torch.cuda.set_per_process_memory_fraction(CUDA_MEMORY_FRACTION)

        if params.data is not None:
            arr = np.asarray(params.data, dtype=np.float32)
            x, y = arr[:, :-1], arr[:, -1]
        else:
            rng = np.random.default_rng(params.random_state)
            x = rng.standard_normal((params.demo_samples, params.demo_features)).astype(
                np.float32
            )
            weights_true = rng.standard_normal(params.demo_features).astype(np.float32)
            scores = x @ weights_true
            if params.task == "classification":
                y = (scores > np.median(scores)).astype(np.float32)
            else:
                y = (scores + 0.1 * rng.standard_normal(params.demo_samples)).astype(
                    np.float32
                )

        x_t = torch.from_numpy(x).to(device)
        y_t = torch.from_numpy(y.astype(np.float32)).to(device)

        out_dim = 1
        layers: list[nn.Module] = []
        in_dim = x.shape[1]
        for width in params.hidden_layers:
            layers.extend([nn.Linear(in_dim, width), nn.ReLU()])
            in_dim = width
        layers.append(nn.Linear(in_dim, out_dim))
        model = nn.Sequential(*layers).to(device)

        if params.task == "classification":
            criterion: nn.Module = nn.BCEWithLogitsLoss()
        else:
            criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=params.learning_rate)

        n = x_t.shape[0]
        loss_curve: list[float] = []
        for _epoch in range(params.epochs):
            permutation = torch.randperm(n, device=device)
            epoch_loss = 0.0
            for start in range(0, n, params.batch_size):
                idx = permutation[start : start + params.batch_size]
                logits = model(x_t[idx]).squeeze(-1)
                loss = criterion(logits, y_t[idx])
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                epoch_loss += float(loss.item()) * len(idx)
            loss_curve.append(epoch_loss / n)

        model.eval()
        with torch.no_grad():
            logits = model(x_t).squeeze(-1)
            metrics: dict[str, float] = {
                "final_loss": loss_curve[-1],
                "epochs": float(params.epochs),
                "n_samples": float(n),
                "n_features": float(x.shape[1]),
                "device_cuda": 1.0 if device == "cuda" else 0.0,
            }
            if params.task == "classification":
                preds = (torch.sigmoid(logits) > 0.5).float()
                metrics["train_accuracy"] = float((preds == y_t).float().mean().item())
            else:
                metrics["train_mse"] = float(
                    nn.functional.mse_loss(logits, y_t).item()
                )

        buffer = io.BytesIO()
        torch.save(model.state_dict(), buffer)
        ctx.logger.info(
            "torch_mlp run %s trained on %s (%d samples)", ctx.run_id, device, n
        )
        return metrics, loss_curve, buffer.getvalue()
