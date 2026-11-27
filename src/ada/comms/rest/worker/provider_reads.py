"""A job's READS of provider data, resolved the way the API routes resolve them.

A scope reads a provider dataset from the provider store when an admin granted the scope that
dataset AND the store holds it (``routes/provider_store.py``, ``docs/design/provider-scope-grants.md``).
The routes ask that per request; a worker job reads through a synchronous storage facade bound to
the job's scope, so a group whose members are provider-published nodes rebuilt nothing: the
manifest is in the store, the job looked in the scope.

:class:`ProviderReads` is that rule for a job, over the very functions the routes use
(``provider_store.dataset_collection_of_key`` names the collection a key belongs to,
``provider_store.resolve_collection`` decides where the scope reads it). The answer is kept per
collection for the job's life, so a group of a hundred members asks the grant table once per
collection. Only READS resolve: a job's outputs are written to its own scope, as before. A job of
the provider store itself reads the store and is unchanged.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any, Optional

from ada.config import logger

__all__ = ["ProviderDataNotGranted", "ProviderReads"]


class ProviderDataNotGranted(FileNotFoundError):
    """A read found nothing in the scope, and what it asked for is provider data the scope holds no
    grant on. A ``FileNotFoundError`` still -- every reader probing for an optional blob catches
    that -- but one whose message says why, instead of "no published manifest"."""


class ProviderReads:
    """Where one job's reads go: the provider store for a granted collection it holds, else the
    job's own scope. Async; the sync facades bridge onto it."""

    def __init__(self, storage, scope, db_pool) -> None:
        self._storage = storage
        self._scope = scope
        self._pool = db_pool
        # The ``ctx`` the provider_store helpers take: they read ``ctx.storage`` only.
        self._ctx = SimpleNamespace(storage=storage)
        self._resolved: dict[str, Any] = {}

    @property
    def active(self) -> bool:
        return self._pool is not None and getattr(self._scope, "kind", None) != "providers"

    @staticmethod
    def _collection_of(key: str) -> Optional[str]:
        from ..routes.provider_store import dataset_collection_of_key

        return dataset_collection_of_key(key)

    async def _collection_scope(self, collection: str):
        name = collection.lower()
        if name not in self._resolved:
            from ..routes.provider_store import resolve_collection

            try:
                where = await resolve_collection(self._ctx, self._pool, self._scope, collection, "geometry")
            except Exception as exc:  # noqa: BLE001 - an unanswerable grant lookup reads the scope, as before
                logger.warning("provider reads: could not resolve %r for %s: %s", collection, self._scope, exc)
                where = self._scope
            self._resolved[name] = where
        return self._resolved[name]

    async def scope_for(self, key: str):
        """The scope a key (or a key prefix) under a provider dataset is read from."""
        if not self.active:
            return self._scope
        collection = self._collection_of(key)
        if collection is None:
            return self._scope
        return await self._collection_scope(collection)

    async def denial(self, key: str) -> Optional[str]:
        """Why ``key`` is not readable here when it is provider data the scope may not read: the
        store holds its collection, and the scope holds no geometry grant on it. None otherwise."""
        if not self.active:
            return None
        collection = self._collection_of(key)
        if collection is None or await self._collection_scope(collection) != self._scope:
            return None
        from ..routes.provider_store import store_holds_geometry

        if not await store_holds_geometry(self._ctx, collection):
            return None
        return (
            f"collection {collection!r} is provider data, published to the provider store, and this scope "
            "holds no geometry grant on it -- an admin grants a scope a provider's collection"
        )

    # -- the reads -------------------------------------------------------------------------------

    async def list_keys(self, prefix: str) -> list[str]:
        where = await self.scope_for(prefix)
        if where != self._scope:
            # The store holds every provider's data: list the prefix, never the whole store.
            return [e.key for e in await self._storage.list_prefix(where, prefix) if e.key.startswith(prefix)]
        return [e.key for e in await self._storage.list(self._scope) if e.key.startswith(prefix)]

    async def list_prefix(self, prefix: str) -> list:
        """The listing entries (key, size, e_tag, ...) under ``prefix``, from where it is read."""
        return await self._storage.list_prefix(await self.scope_for(prefix), prefix)

    async def head(self, key: str) -> Optional[dict]:
        """``key``'s head from where it is read: the store's copy when the grant answers for it,
        else the scope's (``_read``'s rule)."""
        where = await self.scope_for(key)
        if where != self._scope:
            head = await self._storage.head(where, key)
            if head is not None:
                return head
        return await self._storage.head(self._scope, key)

    async def _read(self, key: str, read):
        where = await self.scope_for(key)
        if where != self._scope:
            try:
                return await read(where)
            except (FileNotFoundError, KeyError):
                # The store's copy must exist for the grant to answer (``resolve_blob``'s rule):
                # a key only the scope holds is still the scope's.
                pass
        try:
            return await read(self._scope)
        except (FileNotFoundError, KeyError) as exc:
            reason = await self.denial(key)
            if reason:
                raise ProviderDataNotGranted(f"{key!r} cannot be read in this scope: {reason}") from exc
            raise

    async def get_bytes(self, key: str) -> bytes:
        return await self._read(key, lambda where: self._storage.get_bytes(where, key))

    async def stream_to_path(self, key: str, dest) -> None:
        await self._read(key, lambda where: self._storage.stream_to_path(where, key, pathlib.Path(dest)))
