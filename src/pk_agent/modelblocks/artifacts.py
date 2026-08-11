from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under key; returns the artifact id (== key)."""
        ...

    async def get(self, artifact_id: str) -> bytes: ...

    async def exists(self, artifact_id: str) -> bool: ...


class ArtifactNotFoundError(Exception):
    pass


class LocalArtifactStore:
    """Filesystem-backed store for dev/tests."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        root = self._root.resolve()
        if root not in path.parents and path != root:
            raise ValueError(f"artifact key escapes store root: {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def get(self, artifact_id: str) -> bytes:
        path = self._path(artifact_id)
        if not path.is_file():
            raise ArtifactNotFoundError(artifact_id)
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).is_file()


class MinioArtifactStore:
    """MinIO/S3-backed store. The minio SDK is synchronous, so calls run in
    a thread via asyncio.to_thread."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        from minio import Minio  # imported lazily; app image may not have it

        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await asyncio.to_thread(_ensure)

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        def _put() -> None:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        await asyncio.to_thread(_put)
        return key

    async def get(self, artifact_id: str) -> bytes:
        from minio.error import S3Error

        def _get() -> bytes:
            try:
                response = self._client.get_object(self._bucket, artifact_id)
            except S3Error as exc:
                raise ArtifactNotFoundError(artifact_id) from exc
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_get)

    async def exists(self, artifact_id: str) -> bool:
        from minio.error import S3Error

        def _exists() -> bool:
            try:
                self._client.stat_object(self._bucket, artifact_id)
                return True
            except S3Error:
                return False

        return await asyncio.to_thread(_exists)
