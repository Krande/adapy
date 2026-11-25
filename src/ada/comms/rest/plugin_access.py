"""Scope-gated plugins: a plugin an admin turns on per scope, or per user.

A plugin OPTS IN. Its frontend registration says ``access: "scope"`` (or ``access: {gate: "<id>"}``
when several registrations share one switch), and what it runs on the server says which gate it
belongs to:

* a simulation provider: an ``access_gate`` attribute on the provider object, advertised on its spec
  (``ada.simulation.registry.simulation_provider_specs``);
* a backend plugin: ``access_gate=...`` on ``register_plugin_backend``.

A plugin that declares nothing is never looked up and behaves as before. A gated one is OFF
everywhere until an admin turns it on (``/api/admin/plugin-access``, Admin > Plugins).

The effective rule, for a user working in a scope (:func:`effective_enabled`):

1. the user's own row decides when there is one: on, or an explicit off that wins over an "on" for
   the scope;
2. else the scope's row;
3. else off.

So a plugin is on where it is on for the scope OR for the user, unless that user was explicitly
turned off. It applies to admins too: an admin turns it on for their own scope, or for themselves,
to try it.

Where the gate is enforced on the server: starting a simulation run (``routes/simulations.py`` and
the connections' batch start), and a plugin's job (``routes/plugin_jobs.py``). The refusal is a 403
whose detail is ``{"code": "plugin_access_off", "plugin": <gate>, "scope": <prefix>, "message"}``.

Which server-side thing belongs to which gate is OR-ed across sources, like the plugin-job admin
gate (``routes/plugins.PLUGIN_JOB_ADMIN_SETTING``): the declaration a worker advertises, and an
admin-only setting (:data:`BINDINGS_SETTING`) no worker can influence. A worker on an older build
that advertises nothing therefore cannot open a gate the deployment set. Switches that cannot be
read refuse a gated start (503 ``plugin_access_unavailable``); they never resolve to "allowed".

Without a database there is nowhere to keep the switches and no admin to flip them: the gate is
not enforced there (``enforced: false`` on the read route), as projects and the admin panel are
not offered there either.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, Mapping

from fastapi import HTTPException

from ada.config import logger

from . import db as db_module
from .scope import Scope

#: A gate id: the plugin id the frontend registers, or the id several registrations share.
GATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

#: The refusal's code, in the 403's detail.
ACCESS_OFF_CODE = "plugin_access_off"
#: The code of a 503 when the switches could not be read: never resolved to "allowed".
ACCESS_UNAVAILABLE_CODE = "plugin_access_unavailable"

#: Admin-only setting: what belongs to which gate, beside what workers declare. JSON:
#: ``{"simulation_providers": {"<provider id>": "<gate>"}, "plugin_jobs": {"<plugin id>": "<gate>"}}``.
BINDINGS_SETTING = "admin.plugin_access.bindings"
BINDING_KINDS = ("simulation_providers", "plugin_jobs")


def gate_id(value) -> str | None:
    """``value`` as a gate id, or None when it is not one."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if GATE_ID_PATTERN.match(v) else None


def gate_of_spec(spec: Mapping | None) -> str | None:
    """The gate a provider or plugin spec declares (``access_gate``), or None."""
    return gate_id((spec or {}).get("access_gate"))


class BindingsError(ValueError):
    """The bindings setting is not what it must be."""


def parse_bindings(raw: str | None) -> dict[str, dict[str, str]]:
    """The bindings setting as ``{kind: {id: gate}}``. Empty for an unset or blank value; raises
    :class:`BindingsError` for anything else that is not the documented shape."""
    out: dict[str, dict[str, str]] = {k: {} for k in BINDING_KINDS}
    if raw is None or not raw.strip():
        return out
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise BindingsError(f"not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BindingsError("must be a JSON object")
    for kind, mapping in parsed.items():
        if kind not in BINDING_KINDS:
            raise BindingsError(f"unknown key {kind!r} (known: {', '.join(BINDING_KINDS)})")
        if not isinstance(mapping, dict):
            raise BindingsError(f"{kind!r} must map ids to gate ids")
        for ident, gate in mapping.items():
            g = gate_id(gate)
            if not isinstance(ident, str) or not ident.strip() or g is None:
                raise BindingsError(f"{kind}: {ident!r} -> {gate!r} is not an id and a gate id")
            out[kind][ident.strip()] = g
    return out


def normalise_bindings(raw: str) -> str:
    """The setting as it is stored: checked, and written back canonically."""
    parsed = parse_bindings(raw)
    return json.dumps({k: v for k, v in parsed.items() if v}, sort_keys=True)


async def read_bindings(pool) -> dict[str, dict[str, str]]:
    """The bindings this deployment set; none when they could not be read or parsed.

    Unreadable bindings leave the declarations alone in force (logged), rather than refusing every
    simulation and plugin job of the deployment: the bindings only ever ADD a gate, the value is
    checked when it is written (``routes/admin_settings.py``), and a database that cannot be read
    refuses the gated starts anyway, when their switches are read (:func:`rows_for`, 503)."""
    empty = {k: {} for k in BINDING_KINDS}
    if pool is None:
        return empty
    try:
        return parse_bindings(await db_module.get_setting(pool, BINDINGS_SETTING))
    except Exception:
        logger.exception("plugin access: could not read %s; only what is declared is gated", BINDINGS_SETTING)
        return empty


def gates_for(spec: Mapping | None, ident: str, kind: str, bindings: Mapping[str, Mapping[str, str]]) -> set[str]:
    """Every gate a simulation provider (``kind`` ``simulation_providers``) or a plugin job
    (``plugin_jobs``) belongs to, by any source. Usually one; several when the sources disagree, and
    then every one of them must be on."""
    out = set()
    declared = gate_of_spec(spec)
    if declared:
        out.add(declared)
    bound = (bindings.get(kind) or {}).get(ident)
    if bound:
        out.add(bound)
    return out


def effective_enabled(rows: Iterable[Mapping], gate: str, scope: str, user_sub: str | None) -> bool:
    """Whether ``gate`` is on for ``user_sub`` working in ``scope`` (a storage prefix), by the rows of
    ``plugin_access``: the user's own row decides when there is one, else the scope's, else off."""
    user_row = scope_row = None
    for r in rows:
        if r.get("plugin_id") != gate:
            continue
        if r.get("principal_kind") == "user" and user_sub and r.get("principal") == user_sub:
            user_row = r
        elif r.get("principal_kind") == "scope" and r.get("principal") == scope:
            scope_row = r
    if user_row is not None:
        return bool(user_row.get("enabled"))
    return bool(scope_row is not None and scope_row.get("enabled"))


def access_view(rows: Iterable[Mapping], scope: str, user_sub: str | None) -> dict[str, dict]:
    """``{gate: {enabled, via}}`` for every gate a row bears on: ``via`` is ``user`` when the user's
    own row decided, ``scope`` when the scope's did, null when nothing turned it on."""
    rows = list(rows)
    out: dict[str, dict] = {}
    for gate in sorted({str(r.get("plugin_id")) for r in rows}):
        mine = any(
            r.get("plugin_id") == gate and r.get("principal_kind") == "user" and r.get("principal") == user_sub
            for r in rows
        )
        on = effective_enabled(rows, gate, scope, user_sub)
        out[gate] = {"enabled": on, "via": "user" if mine else ("scope" if on else None)}
    return out


async def rows_for(pool, scope: Scope, user_sub: str | None, gates: Iterable[str] | None = None) -> list[dict]:
    """The rows bearing on ``user_sub`` in ``scope``; 503 when they could not be read."""
    try:
        return await db_module.plugin_access_rows_for(
            pool, scope=scope.prefix(), user_sub=user_sub, plugin_ids=list(gates) if gates is not None else None
        )
    except Exception as exc:
        logger.exception("plugin access: could not read the switches")
        raise HTTPException(
            status_code=503,
            detail={
                "code": ACCESS_UNAVAILABLE_CODE,
                "message": f"could not read which plugins are turned on: {exc}",
            },
        ) from exc


async def require_plugin_access(pool, user, scope: Scope, gates: Iterable[str]) -> None:
    """Refuse (403, ``plugin_access_off``) unless every gate in ``gates`` is on for ``user`` in
    ``scope``. Nothing to check for no gates; not enforced without a database (see the module)."""
    gates = sorted(set(gates))
    if not gates or pool is None:
        return
    sub = getattr(user, "sub", None)
    rows = await rows_for(pool, scope, sub, gates)
    for gate in gates:
        if not effective_enabled(rows, gate, scope.prefix(), sub):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": ACCESS_OFF_CODE,
                    "plugin": gate,
                    "scope": scope.prefix(),
                    "message": (
                        f"the plugin {gate!r} is not turned on for this scope or for you; "
                        "an administrator turns it on under Admin > Plugins"
                    ),
                },
            )


async def require_provider_access(pool, user, scope: Scope, provider: Mapping) -> None:
    """:func:`require_plugin_access` for a run on simulation provider ``provider`` (a listing entry)."""
    if pool is None:
        return
    bindings = await read_bindings(pool)
    await require_plugin_access(
        pool, user, scope, gates_for(provider, str(provider.get("id") or ""), "simulation_providers", bindings)
    )
