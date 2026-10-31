"""What a scope may read of the provider store -- the member-facing side of the provider grants.

An admin grants a scope read access to provider datasets (``/api/admin/provider-grants``,
``db/provider_grants.py``); the scope then reads them through its own routes, and nothing is copied
into it. Two reads of those grants live here:

* ``GET /scopes/{scope}/provider-grants`` -- the grants of ONE scope, for its Sources tab: which
  (provider, collection | every collection) it may read, with ``attributes`` (search, export
  attributes) and ``geometry`` (tree, delivery, builds, design geometry). Any member of the scope
  may ask, through the same access check every ``/scopes/{scope}/...`` route makes; ``request`` is
  not shown -- provider jobs are an admin's. Who granted it and why stays in the admin view.
* ``GET /provider-grants/datasets?provider=`` -- every collection granted for one provider, with
  the scopes it is granted to: who reads what of a provider, for tooling. (Not what a provider
  keeps current -- that is the admin's sweep list, whoever is granted it.) Admin (and admin-minted
  worker) tokens only: the answer names every granted scope.

See ``docs/design/provider-scope-grants.md`` section 3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import Scope
from .deps import require_pool, scope_from_path

router = APIRouter()


def scope_grant_rows(rows: list[dict]) -> list[dict]:
    """A scope's grants as its members see them: dataset and the two read permissions, provider-wide
    grants first (``collection`` null: every collection of the provider). A grant carrying only
    ``request`` reads nothing, so it is not listed."""
    out = [
        {
            "provider": r["provider"],
            "collection": r["collection"],
            "attributes": bool(r.get("attributes")),
            "geometry": bool(r.get("geometry")),
        }
        for r in rows
        if r.get("attributes") or r.get("geometry")
    ]
    out.sort(key=lambda g: (g["provider"], g["collection"] is not None, g["collection"] or ""))
    return out


def dataset_targets(rows: list[dict]) -> list[dict]:
    """``[{collection, target_scopes: [{scope, attributes, geometry}]}]`` -- one entry per granted
    collection (``collection`` null: a provider-wide grant), in collection order."""
    by: dict[str | None, list[dict]] = {}
    for r in rows:
        if not (r.get("attributes") or r.get("geometry")):
            continue
        by.setdefault(r["collection"], []).append(
            {"scope": r["target_scope"], "attributes": bool(r.get("attributes")), "geometry": bool(r.get("geometry"))}
        )
    return [
        {"collection": c, "target_scopes": sorted(targets, key=lambda t: t["scope"])}
        for c, targets in sorted(by.items(), key=lambda kv: (kv[0] is not None, kv[0] or ""))
    ]


@router.get("/scopes/{scope}/provider-grants")
async def api_scope_provider_grants(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """``{grants: [{provider, collection, attributes, geometry}]}`` -- what this scope may read of
    the provider store. Without a database there are no grants: an empty list. The provider store
    itself (an admin's) is granted nothing: it holds everything."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None or scope_obj.kind == "providers":
        return JSONResponse({"grants": []})
    rows = await db_module.list_provider_grants(pool, target_scope=scope_obj.prefix())
    return JSONResponse({"grants": scope_grant_rows(rows)})


@router.get("/provider-grants/datasets")
async def api_provider_grant_datasets(
    request: Request,
    provider: str,
    user: User = Depends(auth_module.require_admin),
) -> JSONResponse:
    """``{provider, datasets: [{collection, target_scopes: [{scope, attributes, geometry}]}]}`` --
    every collection of ``provider`` granted to some scope (``collection`` null: a provider-wide
    grant, meaning every collection the store holds of it). Scopes are storage prefixes."""
    del user  # the admin gate is the point
    provider = (provider or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="'provider' is required")
    pool = require_pool(request)
    rows = await db_module.list_provider_grants(pool, provider=provider)
    return JSONResponse({"provider": provider, "datasets": dataset_targets(rows)})
