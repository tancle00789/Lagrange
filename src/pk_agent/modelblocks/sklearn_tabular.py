from __future__ import annotations

import asyncio
import io
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pk_agent.modelblocks.base import RunContext
from pk_agent.modelblocks.models import ModelBlockSpec, ModelRunResult

MAX_ROWS = 2000
MAX_FEATURES = 50


class SklearnTabularParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: Literal["classification", "regression"] = "classification"
    estimator: Literal["logistic", "random_forest"] = "logistic"
    # Rows of floats; the last column is the target. None -> synthetic demo data.
    data: list[list[float]] | None = None
    test_fraction: float = Field(default=0.25, gt=0.0, lt=0.9)
    random_state: int = 0
    n_estimators: int = Field(default=100, ge=10, le=500)
    max_iter: int = Field(default=200, ge=50, le=2000)
    demo_samples: int = Field(default=400, ge=50, le=MAX_ROWS)
    demo_features: int = Field(default=8, ge=2, le=MAX_FEATURES)

    @model_validator(mode="after")
    def _check(self) -> SklearnTabularParams:
        if self.task == "regression" and self.estimator == "logistic":
            raise ValueError("logistic estimator requires classification task")
        if self.data is not None:
            if not (2 <= len(self.data) <= MAX_ROWS):
                raise ValueError(f"data must have 2..{MAX_ROWS} rows")
            width = len(self.data[0])
            if not (2 <= width <= MAX_FEATURES + 1):
                raise ValueError(
                    f"rows must have 2..{MAX_FEATURES + 1} columns (features + target)"
                )
            if any(len(row) != width for row in self.data):
                raise ValueError("all rows must have the same number of columns")
        return self


class SklearnTabularBlock:
    @property
    def spec(self) -> ModelBlockSpec:
        return ModelBlockSpec(
            name="sklearn_tabular",
            version="1.0.0",
            description=(
                "Train a classical ML model (logistic regression or random forest) "
                "on tabular data (JSON rows, last column = target) or a synthetic "
                "demo dataset. Returns holdout metrics and a serialized model artifact."
            ),
            kind="sklearn",
            gpu_required=False,
            max_runtime_seconds=600,
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return SklearnTabularParams

    async def run(self, params: BaseModel, ctx: RunContext) -> ModelRunResult:
        assert isinstance(params, SklearnTabularParams)
        started = time.monotonic()
        try:
            metrics, model_bytes = await asyncio.to_thread(self._train, params, ctx)
        except Exception as exc:  # surfaced as a failed run, never swallowed
            ctx.logger.exception("sklearn_tabular run %s failed", ctx.run_id)
            return ModelRunResult(
                run_id=ctx.run_id,
                block_name=self.spec.name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        artifact_id = await ctx.artifacts.put(
            ctx.artifact_key("model.joblib"), model_bytes, "application/octet-stream"
        )
        return ModelRunResult(
            run_id=ctx.run_id,
            block_name=self.spec.name,
            status="completed",
            metrics=metrics,
            artifact_ids=[artifact_id],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _train(
        self, params: SklearnTabularParams, ctx: RunContext
    ) -> tuple[dict[str, float], bytes]:
        import joblib
        import numpy as np
        from sklearn.datasets import make_classification, make_regression
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, r2_score
        from sklearn.model_selection import train_test_split

        if params.data is not None:
            arr = np.asarray(params.data, dtype=float)
            x, y = arr[:, :-1], arr[:, -1]
        elif params.task == "classification":
            x, y = make_classification(
                n_samples=params.demo_samples,
                n_features=params.demo_features,
                n_informative=max(2, params.demo_features // 2),
                random_state=params.random_state,
            )
        else:
            x, y = make_regression(
                n_samples=params.demo_samples,
                n_features=params.demo_features,
                random_state=params.random_state,
            )

        x_train, x_test, y_train, y_test = train_test_split(
            x, y, test_size=params.test_fraction, random_state=params.random_state
        )

        if params.estimator == "random_forest":
            model = (
                RandomForestClassifier(
                    n_estimators=params.n_estimators, random_state=params.random_state
                )
                if params.task == "classification"
                else RandomForestRegressor(
                    n_estimators=params.n_estimators, random_state=params.random_state
                )
            )
        else:
            model = LogisticRegression(max_iter=params.max_iter)

        model.fit(x_train, y_train)
        predictions = model.predict(x_test)

        metrics: dict[str, float] = {
            "train_rows": float(len(x_train)),
            "test_rows": float(len(x_test)),
            "n_features": float(x.shape[1]),
        }
        if params.task == "classification":
            metrics["accuracy"] = float(accuracy_score(y_test, predictions))
        else:
            metrics["r2"] = float(r2_score(y_test, predictions))

        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        return metrics, buffer.getvalue()
