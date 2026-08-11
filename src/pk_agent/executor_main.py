from __future__ import annotations

import asyncio
import logging
import signal

from redis.asyncio import Redis

from pk_agent.config import get_settings
from pk_agent.modelblocks.artifacts import (
    ArtifactStore,
    LocalArtifactStore,
    MinioArtifactStore,
)
from pk_agent.modelblocks.broker import run_model_worker
from pk_agent.modelblocks.kv_cache_sim import KvCacheSimBlock
from pk_agent.modelblocks.registry import ModelBlockRegistry
from pk_agent.modelblocks.sklearn_tabular import SklearnTabularBlock
from pk_agent.modelblocks.torch_mlp import TorchMlpBlock
from pk_agent.observability import configure_json_logging

logger = logging.getLogger("pk_agent.executor")


def _build_registry() -> ModelBlockRegistry:
    registry = ModelBlockRegistry()
    registry.register(SklearnTabularBlock())
    registry.register(TorchMlpBlock())
    registry.register(KvCacheSimBlock())
    return registry


async def _amain() -> None:
    settings = get_settings()
    configure_json_logging(settings.log_level)

    artifacts: ArtifactStore
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        minio_store = MinioArtifactStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )
        await minio_store.ensure_bucket()
        artifacts = minio_store
        logger.info("artifact store: minio bucket=%s", settings.minio_bucket)
    else:
        artifacts = LocalArtifactStore(settings.artifacts_local_dir)
        logger.info("artifact store: local dir=%s", settings.artifacts_local_dir)

    client = Redis.from_url(settings.redis_url, decode_responses=True)
    await client.ping()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    registry = _build_registry()
    logger.info(
        "executor starting: blocks=%s", [spec.name for spec in registry.catalog()]
    )
    try:
        await run_model_worker(
            registry,
            artifacts,
            client,
            key_prefix=settings.runtime_key_prefix,
            block_ms=settings.worker_block_ms,
            stop_event=stop_event,
        )
    finally:
        await client.close()
        logger.info("executor stopped")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
