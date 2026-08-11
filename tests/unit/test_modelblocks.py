from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from pk_agent.modelblocks.artifacts import (
    ArtifactNotFoundError,
    LocalArtifactStore,
)
from pk_agent.modelblocks.base import RunContext
from pk_agent.modelblocks.kv_cache_sim import KvCacheSimBlock, KvCacheSimParams
from pk_agent.modelblocks.registry import ModelBlockRegistry
from pk_agent.modelblocks.sklearn_tabular import SklearnTabularBlock, SklearnTabularParams


def _ctx(tmp_path, run_id: str = "run-1") -> RunContext:
    return RunContext(
        tenant_id="tenant-a",
        task_id="task-1",
        run_id=run_id,
        artifacts=LocalArtifactStore(tmp_path),
        logger=logging.getLogger("test"),
    )


async def test_local_artifact_store_roundtrip(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact_id = await store.put(
        "tenant-a/task-1/run-1-data.json", b'{"a": 1}', "application/json"
    )
    assert artifact_id == "tenant-a/task-1/run-1-data.json"
    assert await store.exists(artifact_id)
    assert await store.get(artifact_id) == b'{"a": 1}'
    assert not await store.exists("tenant-a/task-1/missing.json")
    with pytest.raises(ArtifactNotFoundError):
        await store.get("tenant-a/task-1/missing.json")


async def test_local_artifact_store_rejects_traversal(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        await store.put("../evil.bin", b"x", "application/octet-stream")


async def test_kv_cache_sim_real_run(tmp_path) -> None:
    block = KvCacheSimBlock()
    params = KvCacheSimParams(
        num_layers=32, num_kv_heads=8, head_dim=128, context_length=8192
    )
    result = await block.run(params, _ctx(tmp_path))
    assert result.status == "completed"
    assert result.metrics["baseline_memory_gib"] > 0
    assert result.metrics["min_memory_gib"] < result.metrics["baseline_memory_gib"]
    assert len(result.artifact_ids) == 1

    curve = await _ctx(tmp_path).artifacts.get(result.artifact_ids[0])
    import json

    points = json.loads(curve)
    assert [p["compression_ratio"] for p in points] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert points[0]["quality_proxy"] == pytest.approx(1.0)
    assert points[-1]["memory_bytes"] == pytest.approx(points[0]["memory_bytes"] / 16.0)
    assert all(0.0 < p["quality_proxy"] <= 1.0 for p in points)


def test_kv_cache_sim_rejects_bad_ratio() -> None:
    with pytest.raises(ValidationError):
        KvCacheSimParams(compression_ratios=[1.0], quality_gamma=0.0)


async def test_kv_cache_sim_invalid_ratio_fails_run(tmp_path) -> None:
    # Ratio range is enforced at schema level (plan gate), so constructing
    # invalid params raises immediately.
    with pytest.raises(ValidationError, match="compression_ratios"):
        KvCacheSimParams(compression_ratios=[0.5])
    with pytest.raises(ValidationError, match="compression_ratios"):
        KvCacheSimParams(compression_ratios=[])


async def test_sklearn_tabular_demo_classification(tmp_path) -> None:
    pytest.importorskip("sklearn")
    block = SklearnTabularBlock()
    params = SklearnTabularParams(demo_samples=200, demo_features=6, random_state=1)
    result = await block.run(params, _ctx(tmp_path))
    assert result.status == "completed"
    assert 0.0 <= result.metrics["accuracy"] <= 1.0
    assert result.metrics["n_features"] == 6.0
    assert result.artifact_ids[0].endswith("model.joblib")


async def test_sklearn_tabular_inline_regression(tmp_path) -> None:
    pytest.importorskip("sklearn")
    block = SklearnTabularBlock()
    data = [[float(x), float(x * 2), float(x * 3 + 1)] for x in range(60)]
    params = SklearnTabularParams(
        task="regression", estimator="random_forest", data=data, n_estimators=20
    )
    result = await block.run(params, _ctx(tmp_path))
    assert result.status == "completed"
    assert result.metrics["r2"] > 0.5


def test_sklearn_params_reject_bad_inputs() -> None:
    with pytest.raises(ValidationError):
        SklearnTabularParams(task="regression", estimator="logistic")
    with pytest.raises(ValidationError):
        SklearnTabularParams(data=[[1.0, 2.0], [1.0]])  # ragged rows
    with pytest.raises(ValidationError):
        SklearnTabularParams(data=[[1.0]])  # too few rows
    with pytest.raises(ValidationError):
        SklearnTabularParams(**{"task": "classification", "evil": True})  # extra field


async def test_torch_mlp_tiny_run(tmp_path) -> None:
    pytest.importorskip("torch")
    from pk_agent.modelblocks.torch_mlp import TorchMlpBlock, TorchMlpParams

    block = TorchMlpBlock()
    params = TorchMlpParams(
        hidden_layers=[8], epochs=1, demo_samples=64, demo_features=4, batch_size=16
    )
    result = await block.run(params, _ctx(tmp_path))
    assert result.status == "completed"
    assert result.metrics["final_loss"] >= 0.0
    assert len(result.artifact_ids) == 2


def test_registry_register_get_validate() -> None:
    registry = ModelBlockRegistry()
    block = KvCacheSimBlock()
    registry.register(block)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(block)

    assert registry.get("kv_cache_sim") is block
    assert registry.get("nope") is None

    params, error = registry.validate_params("kv_cache_sim", {"num_layers": 16})
    assert error is None and isinstance(params, KvCacheSimParams)

    params, error = registry.validate_params("kv_cache_sim", {"num_layers": -1})
    assert params is None and error is not None

    params, error = registry.validate_params("unknown_block", {})
    assert params is None and error is not None and "unknown" in error

    catalog = registry.catalog_with_schemas()
    assert catalog[0]["name"] == "kv_cache_sim"
    assert "params_schema" in catalog[0]
    assert "kv_cache_sim" in registry.catalog_summary_json()
