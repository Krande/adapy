"""External-source change tracking: ``GET``/``POST /api/scopes/{scope}/source-nodes``.

"Has the external source moved since I exported this?" — see
``migrations/028_source_nodes.sql``. Rows are written by the plugin that
drives the source: through the worker's ``source_nodes`` facade when that
worker has a pool, and through the POST route here when it does not (a
worker outside the cluster reaches this API and nothing else). A deployment
with no Postgres answers 503 rather than an empty list, because "nothing has
changed" and "nobody is recording changes" must not look the same to a
consumer deciding whether to trust an asset.

Needs nothing from the ``create_app`` closure beyond the request (the pool
comes off ``request.app.state``, same as every other DB-backed route before
extraction) — no ``RestContext`` field was needed for this group.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import Scope
from .deps import scope_from_path
from .provider_store import (
    PROVIDER_STORE,
    PROVIDER_STORE_WIRE,
    granted_covers,
    readable_store_datasets,
)

router = APIRouter()

# A POST body is chunked by the writer, so the natural batch is thousands of
# rows; this caps a single request rather than the total a source may ever
# report.
_SOURCE_NODE_WRITE_LIMIT = 10000


def _source_nodes_pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "source-node change tracking needs a database; this deployment runs without "
                "DATABASE_URL. An asset's freshness cannot be answered here."
            ),
        )
    return pool


_ACTIONS = ("added", "modified", "deleted")


def _source_node_json(n) -> dict:
    return {
        "node_ref": n.node_ref,
        "parent_ref": n.parent_ref,
        "name": n.name,
        "last_changed_at": n.last_changed_at.isoformat(),
        "last_changed_by": n.last_changed_by,
        "observed_at": n.observed_at.isoformat(),
        # None for every row written before migration 030, and for any writer
        # that has no opinion -- absent is a normal, complete answer, not a gap.
        "action": getattr(n, "action", None),
    }


def source_dataset(source: str, provider: str | None = None) -> tuple[str | None, str | None]:
    """``(provider, collection)`` a change-feed source names, for the grant check.

    A provider records its feed as ``<provider>:<collection>`` (one source per project, its
    cursor with it). A reader may name the provider itself (``?provider=``) when the source's
    prefix is not the provider id; the collection is still the part after the first ``:``. A
    source without one names no dataset, and is never read through a grant."""
    prefix, sep, collection = (source or "").partition(":")
    if not sep or not collection.strip():
        return None, None
    return (provider or prefix).strip() or None, collection.strip()


def _source_row_json(row, *, read_from: str | None = None) -> dict:
    out = {
        "source": row["source"],
        "nodes": row["nodes"],
        "last_changed_at": row["last_changed_at"].isoformat() if row["last_changed_at"] else None,
        "observed_at": row["observed_at"].isoformat() if row["observed_at"] else None,
    }
    if read_from is not None:
        out["read_from"] = read_from
    return out


async def _feed_scope(scope: str, request: Request, user: User = Depends(auth_module.current_user)) -> Scope:
    """``scope_from_path``, with the provider store's refusal spelled out.

    A provider job runs in the store, so its change feed is recorded under ``providers``: by the
    worker's pool facade (no HTTP at all) or, for a worker with no pool, through this route with
    the worker's bearer token. The store is an admin's, so that token must be an admin's (a CLI
    token minted by an admin, as for a CLI publish into the store). Nothing here opens the store to
    anyone else: a member reads the store's feed only through a grant on their own scope (GET)."""
    if scope == PROVIDER_STORE_WIRE and not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=403,
            detail=(
                "the provider store's change feed is recorded by provider jobs: a worker without a database "
                "records it through this API with an ADMIN token (ADA_VIEWER_TOKEN minted by an admin). "
                "Members read it through the grants on their own scope"
            ),
        )
    return await scope_from_path(scope, request, user)


@router.get("/scopes/{scope}/source-nodes")
async def api_source_nodes(
    request: Request,
    scope_obj: Scope = Depends(_feed_scope),
    source: str | None = None,
    since: str | None = None,
    refs: str | None = None,
    limit: int = 1000,
    provider: str | None = None,
) -> JSONResponse:
    """Change state for one scope's external-source nodes.

    Three shapes, one route, because they answer one question at different
    widths:

    * no ``source`` -- which sources are recorded here at all, with counts.
      What a consumer reads before it knows any refs.
    * ``refs=a,b,c`` -- those nodes, in one round trip. The freshness check:
      a caller holding published assets asks about every one at once rather
      than N times over a slow link.
    * ``since=<iso8601>`` -- what has moved since a cursor the caller keeps.
      The polling shape.

    THROUGH GRANTS. Provider jobs record their feed in the provider store,
    once. A scope granted a dataset (attributes or geometry) reads the
    store's rows of that dataset's source -- ``<provider>:<collection>``, or
    name the provider with ``?provider=`` -- and its own rows otherwise,
    including its own copy from before the store until the store has rows of
    that source. The listing (no ``source``) adds the granted store sources
    beside the scope's own, each marked ``read_from: "providers"``; a source
    in both answers from the store. ``read_from`` on a node answer says which
    one answered.
    """
    pool = _source_nodes_pool(request)
    # prefix(), not str(). `Scope` is a dataclass, so str() is its REPR --
    # "Scope(kind='shared', id=None)" -- which is not an identifier: it
    # changes shape if a field is ever added or reordered, and every row
    # keyed by the old spelling is orphaned without anything reporting it.
    # prefix() is the canonical scope key the storage layer already uses.
    scope_str = scope_obj.prefix()
    granted = await readable_store_datasets(pool, scope_obj)

    if not source:
        own = await db_module.list_source_node_sources(pool, scope=scope_str)
        if not granted:
            return JSONResponse({"scope": scope_str, "sources": [_source_row_json(row) for row in own]})
        store_rows = [
            row
            for row in await db_module.list_source_node_sources(pool, scope=PROVIDER_STORE.prefix())
            if granted_covers(granted, *source_dataset(row["source"], provider))
        ]
        from_store = {row["source"] for row in store_rows}
        return JSONResponse(
            {
                "scope": scope_str,
                "sources": [_source_row_json(row) for row in own if row["source"] not in from_store]
                + [_source_row_json(row, read_from=PROVIDER_STORE_WIRE) for row in store_rows],
            }
        )

    # Which rows answer: the store's for a granted dataset it has rows of, else the scope's own.
    read_key = scope_str
    if granted and granted_covers(granted, *source_dataset(source, provider)):
        store_sources = await db_module.list_source_node_sources(pool, scope=PROVIDER_STORE.prefix())
        if any(row["source"] == source for row in store_sources):
            read_key = PROVIDER_STORE.prefix()
    read_from = PROVIDER_STORE_WIRE if read_key != scope_str else None

    limit = max(1, min(int(limit or 1000), 10000))

    if refs:
        wanted = [r.strip() for r in refs.split(",") if r.strip()]
        if len(wanted) > limit:
            raise HTTPException(status_code=400, detail=f"at most {limit} refs per request, got {len(wanted)}")
        found = await db_module.get_source_nodes(pool, scope=read_key, source=source, node_refs=wanted)
        by_ref = {n.node_ref: n for n in found}
        return JSONResponse(
            {
                "scope": scope_str,
                "source": source,
                "read_from": read_from,
                "nodes": [_source_node_json(by_ref[r]) for r in wanted if r in by_ref],
                # Named rather than merely absent: a ref nobody has recorded
                # is not a ref that has not changed, and a caller must be
                # able to tell those apart before trusting an asset.
                "unknown": [r for r in wanted if r not in by_ref],
            }
        )

    parsed_since = None
    if since:
        try:
            parsed_since = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"since is not an ISO-8601 timestamp: {since!r}")

    nodes = await db_module.list_source_nodes_changed_since(
        pool, scope=read_key, source=source, since=parsed_since, limit=limit
    )
    return JSONResponse(
        {
            "scope": scope_str,
            "source": source,
            "read_from": read_from,
            "since": parsed_since.isoformat() if parsed_since else None,
            "nodes": [_source_node_json(n) for n in nodes],
            "truncated": len(nodes) >= limit,
        }
    )


def _parse_source_node(raw, index: int) -> dict:
    """One posted node -> the dict ``record_source_nodes`` takes.

    Strict about ``last_changed_at`` on purpose. That column only ever
    moves FORWARD (``GREATEST`` in the upsert), so a timestamp written
    wrong is not a transient wrong answer -- it is permanent, and no later
    correct observation can pull it back. A naive timestamp is the way that
    happens in practice: a writer in CET posting local wall-clock is read as
    UTC, lands up to two hours in the FUTURE, and every consumer then
    believes its export is stale forever. Cheap to reject, unfixable to
    accept, so an offset is required rather than assumed.
    """
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail=f"nodes[{index}] must be an object")
    node_ref = (str(raw.get("node_ref") or "")).strip()
    if not node_ref:
        raise HTTPException(status_code=400, detail=f"nodes[{index}] is missing node_ref")
    changed = raw.get("last_changed_at")
    if isinstance(changed, str):
        try:
            changed = datetime.datetime.fromisoformat(changed.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"nodes[{index}].last_changed_at is not an ISO-8601 timestamp: {changed!r}",
            )
    if not isinstance(changed, datetime.datetime):
        raise HTTPException(
            status_code=400,
            detail=f"nodes[{index}] is missing last_changed_at (an ISO-8601 timestamp)",
        )
    if changed.tzinfo is None or changed.tzinfo.utcoffset(changed) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"nodes[{index}].last_changed_at has no UTC offset. This column only moves "
                "forward, so a mis-read timestamp is permanent -- send an offset "
                "(…Z or …+02:00) rather than local wall-clock."
            ),
        )

    def _opt(field: str):
        val = raw.get(field)
        if val is None:
            return None
        val = str(val).strip()
        return val or None

    action = _opt("action")
    if action is not None and action not in _ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"nodes[{index}].action {action!r} not in {_ACTIONS} (omit it if the writer has no opinion)",
        )

    return {
        "node_ref": node_ref,
        "parent_ref": _opt("parent_ref"),
        "name": _opt("name"),
        "last_changed_at": changed,
        "last_changed_by": _opt("last_changed_by"),
        "action": action,
    }


@router.post("/scopes/{scope}/source-nodes")
async def api_source_nodes_record(
    request: Request,
    scope_obj: Scope = Depends(_feed_scope),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Record observed source nodes. Body: ``{source, nodes: [...]}``.

    WHY A ROUTE EXISTS AT ALL. Until now the only way to write this table
    was the worker's ``source_nodes`` facade, which is a database pool --
    so recording required the writer to hold Postgres credentials. That is
    fine for a worker inside the cluster and wrong for one outside it. A
    worker that joins the job queue from another network is a supported
    deployment, and such a worker deliberately runs without
    ``DATABASE_URL``: one fewer credential, and no route from outside to
    the database. Change tracking was therefore silently inert on exactly
    the workers most likely to be driving an external source -- the plugin
    warned, recorded nothing, and its caller re-scanned from cold forever.
    A writer that can already reach this API over HTTPS should not need a
    second, far more powerful credential to say "this node moved".

    Authorisation is the scope's own: ``scope_from_path`` has already
    rejected a caller who is not a member of a project scope, which is the
    same gate that decides who may write that scope's blobs. The provider
    store's feed (``providers`` -- where every provider job records) is
    written with an admin's token only; see ``_feed_scope``.

    Nodes are capped per request; the writer chunks. A roll-up stamps every
    node ABOVE a changed leaf (the writer's job -- see
    ``record_source_nodes``), so the natural batch is thousands of rows and
    an uncapped body is a proxy-sized surprise rather than a feature.
    """
    pool = _source_nodes_pool(request)
    scope_str = scope_obj.prefix()  # prefix(), not str() -- see the GET above.

    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    source = (str(body.get("source") or "")).strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    raw_nodes = body.get("nodes")
    if not isinstance(raw_nodes, list):
        raise HTTPException(status_code=400, detail="nodes must be a list")
    if len(raw_nodes) > _SOURCE_NODE_WRITE_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"at most {_SOURCE_NODE_WRITE_LIMIT} nodes per request, got {len(raw_nodes)}. "
                "Post in chunks; the upsert is idempotent."
            ),
        )
    nodes = [_parse_source_node(n, i) for i, n in enumerate(raw_nodes)]

    # An empty list is accepted rather than 400: a sweep that found nothing
    # changed is a normal outcome, and making the writer special-case it
    # invites the writer to skip the call and lose the "I ran" signal.
    written = await db_module.record_source_nodes(pool, scope=scope_str, source=source, nodes=nodes)
    logger.info(
        "source-nodes: %s recorded %d node(s) for source=%s scope=%s",
        getattr(user, "sub", "?"),
        written,
        source,
        scope_str,
    )
    return JSONResponse({"scope": scope_str, "source": source, "recorded": written})
