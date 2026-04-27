"""Storage abstraction backed by obstore.

obstore (the Python binding to the Rust object_store crate) gives us a
single async API across S3, GCS, Azure, and the local filesystem. The
viewer only needs list / get / stream so we keep the wrapper small.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

import obstore as obs
from obstore.store import LocalStore, S3Store

from .config import Settings


@dataclass(frozen=True)
class FileEntry:
    key: str
    size: int


class Storage:
    def __init__(self, store, prefix: str) -> None:
        # `store` is whichever obstore backend implementation we built.
        # LocalStore and S3Store don't share a public Protocol but expose
        # the same get_async / list / stream surface.
        self._store = store
        # Trim trailing slashes so callers can treat it as a directory.
        self._prefix = prefix.strip("/")

    @classmethod
    def from_settings(cls, settings: Settings) -> "Storage":
        if settings.storage_kind == "s3":
            assert settings.s3 is not None
            cfg = settings.s3
            kwargs: dict[str, object] = {
                "region": cfg.region,
                "virtual_hosted_style_request": cfg.virtual_hosted_style,
            }
            if cfg.endpoint:
                kwargs["endpoint"] = cfg.endpoint
                # Object_store / reqwest reject plain http:// schemes by
                # default. Cluster-local Garage / MinIO listen on http,
                # so we opt in when the endpoint clearly is http.
                if cfg.endpoint.lower().startswith("http://"):
                    kwargs["allow_http"] = True
            if cfg.access_key_id:
                kwargs["access_key_id"] = cfg.access_key_id
            if cfg.secret_access_key:
                kwargs["secret_access_key"] = cfg.secret_access_key
            store = S3Store(cfg.bucket, **kwargs)
            return cls(store, prefix=cfg.prefix)

        assert settings.local is not None
        return cls(LocalStore(settings.local.path), prefix=settings.local.prefix)

    def _full_key(self, key: str) -> str:
        key = key.lstrip("/")
        if not self._prefix:
            return key
        return f"{self._prefix}/{key}"

    def _strip_prefix(self, full: str) -> str:
        if self._prefix and full.startswith(self._prefix + "/"):
            return full[len(self._prefix) + 1 :]
        return full

    async def list(self) -> list[FileEntry]:
        entries: list[FileEntry] = []
        # obs.list returns a ListStream of pages; each page is a Sequence
        # of ObjectMeta dicts with keys "path" and "size".
        stream = obs.list(self._store, prefix=self._prefix or None)
        async for page in stream:
            for meta in page:
                entries.append(
                    FileEntry(key=self._strip_prefix(meta["path"]), size=int(meta["size"]))
                )
        return entries

    async def get_bytes(self, key: str) -> bytes:
        result = await self._store.get_async(self._full_key(key))
        return bytes(await result.bytes_async())

    async def put_bytes(self, key: str, data: bytes) -> None:
        await obs.put_async(self._store, self._full_key(key), data)

    async def exists(self, key: str) -> bool:
        try:
            await self._store.head_async(self._full_key(key))
        except FileNotFoundError:
            return False
        return True

    async def open_stream(self, key: str) -> AsyncIterator[bytes]:
        """Open a byte stream eagerly: raises FileNotFoundError immediately
        if the key is missing, then returns an async iterator over chunks.
        """
        result = await self._store.get_async(self._full_key(key))

        async def _gen() -> AsyncIterator[bytes]:
            async for chunk in result.stream():
                yield bytes(chunk)

        return _gen()
