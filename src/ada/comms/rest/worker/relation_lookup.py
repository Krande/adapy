"""The worker's :class:`ada.assets.relation_lookup.NodeRelationLookup`: over the database pool when it
has one, over the API when it has credentials instead, otherwise none."""

from __future__ import annotations

import asyncio

from ada.assets.relation_lookup import NodeRelations

__all__ = ["PoolRelationLookup", "RestRelationLookup", "relation_lookup_for"]

# Node ids plus full names one API request may name (``db.node_relations.LOOKUP_MAX``); kept here so a
# worker without a database driver can import this module.
LOOKUP_MAX = 5000


def _chunks(ids: list[str], names: list[str], size: int):
    items = [("id", v) for v in ids] + [("name", v) for v in names]
    for i in range(0, len(items), size):
        chunk = items[i : i + size]
        yield [v for k, v in chunk if k == "id"], [v for k, v in chunk if k == "name"]


class PoolRelationLookup:
    """One query per chunk, bridged onto the worker's loop. Reads the job scope's own sets, the shared
    scope's and those of the share origins every user may read (a job carries no user)."""

    def __init__(self, pool, scope, loop, *, chunk: int = LOOKUP_MAX):
        self._pool, self._scope, self._loop, self._chunk = pool, scope, loop, chunk
        self._scopes: list[str] | None = None

    async def _lookup(self, collection, ids, names) -> dict:
        from ..db.attribute_index import readable_share_origins
        from ..db.node_relations import readable_scopes, relations_for

        scope = self._scope.prefix()
        if self._scopes is None:
            self._scopes = readable_scopes(scope, await readable_share_origins(self._pool, scope=scope, user_sub=""))
        return await relations_for(
            self._pool, scopes=self._scopes, collection=collection, node_ids=ids, full_names=names
        )

    def relations(self, collection, *, node_ids=(), full_names=()) -> NodeRelations:
        answer = NodeRelations()
        for ids, names in _chunks(list(node_ids), list(full_names), self._chunk):
            raw = asyncio.run_coroutine_threadsafe(self._lookup(collection, ids, names), self._loop).result()
            answer = answer.merged(NodeRelations.from_dict(raw))
        return answer


class RestRelationLookup:
    """The same lookup through ``POST /api/scopes/{scope}/assets/relations``, chunked."""

    def __init__(self, base_url: str, token: str, scope, *, chunk: int = LOOKUP_MAX):
        from .source_nodes import _RestSourceNodesRecorder

        self._http = _RestSourceNodesRecorder(base_url, token, scope)
        self._base = base_url.rstrip("/")
        self._scope = scope
        self._chunk = chunk

    def _url(self) -> str:
        from urllib.parse import quote

        return f"{self._base}/api/scopes/{quote(self._scope.wire(), safe=':')}/assets/relations"

    def relations(self, collection, *, node_ids=(), full_names=()) -> NodeRelations:
        answer = NodeRelations()
        for ids, names in _chunks(list(node_ids), list(full_names), self._chunk):
            body = {"collection": collection, "node_ids": ids, "full_names": names}
            answer = answer.merged(NodeRelations.from_dict(self._http._request(self._url(), body)))
        return answer


def relation_lookup_for(db_pool, scope, loop):
    """The lookup this worker can offer, or None (then an export is written without relations)."""
    if db_pool is not None:
        return PoolRelationLookup(db_pool, scope, loop)
    from .source_nodes import _rest_source_nodes_config

    cfg = _rest_source_nodes_config()
    return None if cfg is None else RestRelationLookup(cfg[0], cfg[1], scope)
