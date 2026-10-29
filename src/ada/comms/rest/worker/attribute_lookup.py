"""The worker's :class:`ada.assets.attribute_lookup.NodeAttributeLookup`: over the database pool
when it has one, over the API when it has credentials instead, otherwise none."""

from __future__ import annotations

import asyncio
import time

from ada.assets.attribute_lookup import NodeAttributes
from ada.config import logger

__all__ = ["PoolAttributeLookup", "RestAttributeLookup", "attribute_lookup_for"]

# Node ids plus full names one API request may name (``db.attribute_index.NODES_LOOKUP_MAX``);
# kept here so a worker without a database driver can import this module.
NODES_LOOKUP_MAX = 5000


def _split(ids: list[str], names: list[str], size: int):
    items = [("id", v) for v in ids] + [("name", v) for v in names]
    for i in range(0, len(items), size):
        chunk = items[i : i + size]
        yield [v for k, v in chunk if k == "id"], [v for k, v in chunk if k == "name"]


class PoolAttributeLookup:
    """One query per readable partition of the collection, bridged onto the worker's loop.

    Reads the job scope's own partitions and those shared into it from the shared scope, which
    every user may read; a job carries no user to widen that by."""

    def __init__(self, pool, scope, loop):
        self._pool, self._scope, self._loop = pool, scope, loop
        self._shared_from: list[str] | None = None

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _lookup(self, collection, ids, names, keys, ancestors) -> dict:
        from ..db.attribute_index import nodes_attributes, readable_share_origins

        scope = self._scope.prefix()
        if self._shared_from is None:
            self._shared_from = await readable_share_origins(self._pool, scope=scope, user_sub="")
        return await nodes_attributes(
            self._pool,
            scope=scope,
            collection=collection,
            node_ids=ids,
            full_names=names,
            keys=keys,
            ancestors=ancestors,
            shared_from=self._shared_from,
        )

    def lookup(self, collection, *, node_ids=(), full_names=(), keys=None, ancestors=0) -> NodeAttributes:
        t0 = time.perf_counter()
        ids, names = list(node_ids), list(full_names)
        raw = self._run(self._lookup(collection, ids, names, list(keys) if keys is not None else None, ancestors))
        answer = NodeAttributes.from_dict(raw)
        logger.debug(
            "attribute lookup %s: %d asked, %d found, %d ancestors in %.0f ms",
            collection,
            len(ids) + len(names),
            len(answer.nodes),
            len(answer.ancestors),
            (time.perf_counter() - t0) * 1000,
        )
        return answer


class RestAttributeLookup:
    """The same lookup through ``POST /api/scopes/{scope}/assets/search/nodes``, chunked."""

    def __init__(self, base_url: str, token: str, scope, *, chunk: int = NODES_LOOKUP_MAX):
        from .source_nodes import _RestSourceNodesRecorder

        self._http = _RestSourceNodesRecorder(base_url, token, scope)
        self._base = base_url.rstrip("/")
        self._scope = scope
        self._chunk = chunk

    def _url(self) -> str:
        from urllib.parse import quote

        return f"{self._base}/api/scopes/{quote(self._scope.wire(), safe=':')}/assets/search/nodes"

    def lookup(self, collection, *, node_ids=(), full_names=(), keys=None, ancestors=0) -> NodeAttributes:
        answer = NodeAttributes()
        for ids, names in _split(list(node_ids), list(full_names), self._chunk):
            body = {"collection": collection, "node_ids": ids, "full_names": names, "ancestors": ancestors}
            if keys is not None:
                body["keys"] = list(keys)
            answer = answer.merged(NodeAttributes.from_dict(self._http._request(self._url(), body)))
        return answer


def attribute_lookup_for(db_pool, scope, loop):
    """The lookup this worker can offer, or None (then a reader works without one)."""
    if db_pool is not None:
        return PoolAttributeLookup(db_pool, scope, loop)
    from .source_nodes import _rest_source_nodes_config

    cfg = _rest_source_nodes_config()
    return None if cfg is None else RestAttributeLookup(cfg[0], cfg[1], scope)
