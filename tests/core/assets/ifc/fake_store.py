"""An in-memory stand-in for a scope's object storage -- the same shape as
``tests/core/assets/fixture_provider/provider.py::FakeStore``, duplicated rather than imported
from it: a provider's tests should not depend on another provider's test-only fixture module."""

from __future__ import annotations

from typing import Iterable

from ada.assets.published import StorageReader


class FakeStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    def list_prefix(self, prefix: str) -> Iterable[str]:
        return [k for k in sorted(self.blobs) if k.startswith(prefix)]

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.blobs[key]
        except KeyError:
            raise FileNotFoundError(key) from None

    def reader(self) -> StorageReader:
        return StorageReader(list_prefix=self.list_prefix, get_bytes=self.get_bytes)


class FakeSyncStorageFacade:
    """The subset of ``worker/source_nodes.py::_SyncStorageFacade`` a builder actually calls:
    ``get_bytes`` / ``put_bytes(key, data, content_encoding=None)``."""

    def __init__(self, store: FakeStore) -> None:
        self._store = store

    def get_bytes(self, key: str) -> bytes:
        return self._store.get_bytes(key)

    def put_bytes(self, key: str, data: bytes, content_encoding: str | None = None) -> None:
        self._store.put_bytes(key, data)
