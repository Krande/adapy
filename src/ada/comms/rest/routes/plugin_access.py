"""Scope-gated plugins: which plugin is turned on where (migration 056, ``ada.comms.rest.plugin_access``).

Member side:

``GET /scopes/{scope}/plugin-access``
    ``{scope, enforced, enabled: [gate], plugins: {gate: {enabled, via}}}`` -- the gated plugins turned
    on for the CALLER in this scope (``via``: ``user`` when the caller's own switch decided, ``scope``
    when the scope's did). A gated plugin not listed in ``enabled`` is off. ``enforced`` is false on a
    server without a database, where nothing is gated. The viewer asks it again on every scope switch.

Admin side (``/api/admin``, admin-only like every route there):

``GET    /admin/plugin-access?principal_kind=&principal=&plugin_id=``
    ``{rows, gates}``: the switches, each filter optional, and every gate this server knows of -- what
    the online simulation providers and backend plugins declare, the bindings setting, and every gate
    a switch names -- each with where it was seen.
``PUT    /admin/plugin-access``  ``{principal_kind: scope | user, principal, plugin_id, enabled, note?}``
    turn a plugin on or off for a scope (a storage prefix: ``shared``, ``projects/<uuid>``,
    ``users/<sub>``) or for a user (an OIDC sub). For a user, ``enabled: false`` is an explicit off
    that wins over an "on" for the scope.
``DELETE /admin/plugin-access``  ``{principal_kind, principal, plugin_id}``
    remove the switch: back to the default (off), or for a user to what the scope says.

Every change is logged and written to the audit log (``plugin_access_set`` / ``plugin_access_clear``,
``key`` ``<kind>:<principal>:<plugin>``), and the row keeps who changed it last.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..plugin_access import (
    BINDINGS_SETTING,
    access_view,
    gate_id,
    gate_of_spec,
    read_bindings,
    rows_for,
)
from ..scope import Scope
from .deps import RestContext, require_pool, rest_context, scope_from_path

router = APIRouter()
admin_router = APIRouter()

#: The kinds of scope a plugin may be turned on for. Not a corpus and not the provider store: nobody
#: works in either with a plugin's UI (a user switch still covers an admin working there).
SCOPE_KINDS = ("shared", "project", "user")
_PRINCIPAL_MAX = 255


@router.get("/scopes/{scope}/plugin-access")
async def api_scope_plugin_access(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return JSONResponse({"scope": scope_obj.prefix(), "enforced": False, "enabled": [], "plugins": {}})
    rows = await rows_for(pool, scope_obj, user.sub)
    view = access_view(rows, scope_obj.prefix(), user.sub)
    return JSONResponse(
        {
            "scope": scope_obj.prefix(),
            "enforced": True,
            "enabled": sorted(g for g, v in view.items() if v["enabled"]),
            "plugins": view,
        }
    )


# --- admin ---------------------------------------------------------------------------------------


async def _body(request: Request) -> dict:
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


def parse_ref(body: dict) -> dict:
    """``{principal_kind, principal, plugin_id}`` from a body, shape-checked (400s)."""
    kind = body.get("principal_kind")
    if kind not in ("scope", "user"):
        raise HTTPException(status_code=400, detail="'principal_kind' is 'scope' or 'user'")
    principal = body.get("principal")
    if not isinstance(principal, str) or not principal.strip():
        raise HTTPException(status_code=400, detail="'principal' is required (a non-empty string)")
    principal = principal.strip()
    if len(principal) > _PRINCIPAL_MAX or any(ord(ch) < 32 for ch in principal):
        raise HTTPException(status_code=400, detail="'principal' is not a scope prefix or a user id")
    plugin_id = gate_id(body.get("plugin_id"))
    if plugin_id is None:
        raise HTTPException(
            status_code=400, detail="'plugin_id' is required: lower-case letters, digits, '.', '_' or '-'"
        )
    if kind == "scope":
        try:
            target = Scope.from_prefix(principal)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if target.kind not in SCOPE_KINDS:
            raise HTTPException(status_code=400, detail=f"a plugin cannot be turned on for a {target.kind} scope")
        if target.kind == "project":
            try:
                uuid.UUID(target.id or "")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="a project scope is projects/<uuid>") from exc
    return {"principal_kind": kind, "principal": principal, "plugin_id": plugin_id}


def _audit_scope(ref: dict) -> Scope:
    """The scope an audit row of a change is filed under: the scope switched, or the user's own."""
    if ref["principal_kind"] == "scope":
        return Scope.from_prefix(ref["principal"])
    return Scope.user(ref["principal"])


def _audit_key(ref: dict) -> str:
    return f"{ref['principal_kind']}:{ref['principal']}:{ref['plugin_id']}"


async def known_gates(ctx: RestContext, request: Request, pool, rows: list[dict]) -> list[dict]:
    """Every gate this server knows of, ``[{id, sources: [...]}]`` by id. A source is
    ``simulation_provider:<id>``, ``plugin:<id>``, ``binding:<kind>:<id>`` or ``switch``."""
    from .plugins import online_plugin_specs
    from .simulations import _providers

    seen: dict[str, set[str]] = {}

    def add(gate: str | None, source: str) -> None:
        if gate:
            seen.setdefault(gate, set()).add(source)

    try:
        for p in await _providers(ctx, request):
            add(gate_of_spec(p), f"simulation_provider:{p.get('id')}")
    except Exception:  # noqa: BLE001 - the listing is a convenience; the switches still work
        logger.exception("plugin access: could not list the simulation providers")
    try:
        for slug, spec in (await online_plugin_specs(ctx)).items():
            add(gate_of_spec(spec), f"plugin:{slug}")
    except Exception:  # noqa: BLE001
        logger.exception("plugin access: could not list the backend plugins")
    for kind, mapping in (await read_bindings(pool)).items():
        for ident, gate in mapping.items():
            add(gate, f"binding:{kind}:{ident}")
    for r in rows:
        add(r["plugin_id"], "switch")
    return [{"id": g, "sources": sorted(s)} for g, s in sorted(seen.items())]


@admin_router.get("/plugin-access")
async def admin_plugin_access(
    request: Request,
    principal_kind: str | None = None,
    principal: str | None = None,
    plugin_id: str | None = None,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    pool = require_pool(request)
    rows = await db_module.list_plugin_access(
        pool, principal_kind=principal_kind or None, principal=principal or None, plugin_id=plugin_id or None
    )
    every = rows if not (principal_kind or principal or plugin_id) else await db_module.list_plugin_access(pool)
    return JSONResponse(
        {"rows": rows, "gates": await known_gates(ctx, request, pool, every), "bindings_setting": BINDINGS_SETTING}
    )


@admin_router.put("/plugin-access")
async def admin_plugin_access_put(
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    pool = require_pool(request)
    body = await _body(request)
    ref = parse_ref(body)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="'enabled' must be true or false")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="'note' must be a string")
    target = _audit_scope(ref)
    if target.kind == "project" and not await db_module.project_exists(pool, target.id):
        raise HTTPException(status_code=404, detail=f"no project {target.id!r}")
    row = await db_module.set_plugin_access(pool, **ref, enabled=enabled, granted_by=user.sub, note=note)
    logger.info(
        "plugin access by %s: %s %s for %s %s",
        user.sub,
        ref["plugin_id"],
        "on" if enabled else "off",
        ref["principal_kind"],
        ref["principal"],
    )
    await ctx.audit(
        request,
        user,
        target,
        "plugin_access_set",
        key=_audit_key(ref),
        target_format="on" if enabled else "off",
        status="ok",
    )
    return JSONResponse({"row": row})


@admin_router.delete("/plugin-access")
async def admin_plugin_access_delete(
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    pool = require_pool(request)
    ref = parse_ref(await _body(request))
    removed = await db_module.clear_plugin_access(pool, **ref)
    if removed:
        logger.info(
            "plugin access cleared by %s: %s for %s %s",
            user.sub,
            ref["plugin_id"],
            ref["principal_kind"],
            ref["principal"],
        )
        await ctx.audit(request, user, _audit_scope(ref), "plugin_access_clear", key=_audit_key(ref), status="ok")
    return JSONResponse({"removed": removed})
