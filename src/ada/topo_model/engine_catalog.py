"""Per-engine *capability* flags, announced by workers.

A procedural engine may support features the built-in engines don't. The one
capability flagged here is ``supports_grouping``: whether the engine understands
the cellbuilder's cell GROUPS (a group is one structure compiled with its own
blueprint). The built-in engines do NOT — ``adapy-default`` compiles a single
model-level blueprint (and tolerates-but-ignores any ``groups``/``STRUCTURE_NAME``
a doc carries), ``echo`` renders raw boxes. A capability engine
advertises ``supports_grouping=True`` by importing a module that calls
:func:`register_procedural_engine_capabilities` at worker start — see
``ADA_WORKER_PRELOAD`` — exactly like :func:`register_procedural_blueprint`.

Each worker advertises the registry in its heartbeat (``procedural_engine_specs``);
the viewer's ``/procedural-engines`` endpoint folds a live worker's flags onto the
matching engine summary by slug (built-ins carry their static flags directly), so
the frontend can gate the Groups UI on ``supports_grouping`` without hardcoding
any engine slug. The same registration says whether an engine has an Excel format
(``supports_excel``) and which handlers write and read it, so the export/import
routes gate on that rather than on a list of slugs.
"""

from __future__ import annotations

# The predicate lives under ada.core (below both ada.comms and ada.topo_model)
# so the REST API can reach it without ada.topo_model, which the slim viewer
# runtime does not ship, and ada.topo_model doesn't have to reach into
# ada.comms to get it back. Re-exported here (and in ada.comms.engine_specs)
# so the two sides cannot drift and existing importers keep working.
from ada.core.engine_specs import is_offerable

from .engines import BUILTIN_ENGINES

__all__ = [
    "register_procedural_engine_capabilities",
    "procedural_engine_specs",
    "is_offerable",
]

# engine slug -> {"slug", "supports_grouping"}. Insertion-ordered; last writer per
# slug wins (a re-imported preload module just replaces its own entry).
_ENGINE_CAPABILITY_REGISTRY: dict[str, dict] = {}


def register_procedural_engine_capabilities(
    slug: str,
    *,
    supports_grouping: bool = False,
    name: str | None = None,
    description: str | None = None,
    entrypoint: str | None = None,
    worker_capability: str | None = None,
    export_entrypoint: str | None = None,
    import_entrypoint: str | None = None,
    supports_excel: bool | None = None,
) -> None:
    """Register (or replace) what an engine advertises. Idempotent by ``slug``.

    ``slug`` and ``supports_grouping`` alone register capability FLAGS for an
    engine the viewer already knows about (a built-in, or a row an admin
    created). That is the original behaviour and is unchanged.

    Passing ``name`` AND ``entrypoint`` additionally makes the engine
    SELF-ADVERTISING: the viewer will offer it wherever a live worker announces
    it, with no database row and no admin step. This is the difference between
    "an engine that exists supports grouping" and "this engine exists at all".

    ``entrypoint`` is the usual ``module:callable``, resolved by the worker at
    job time. ``worker_capability`` is the pool the job must be routed to; leave
    it unset for an engine any worker can run.

    ``export_entrypoint`` / ``import_entrypoint`` name the engine's Excel handlers
    (``module:callable``, as a database row's manifest names them); left unset,
    they are the ``export_xlsx`` / ``import_xlsx`` siblings of ``entrypoint``.
    ``supports_excel=False`` says the engine has no Excel format at all, and the
    export/import routes refuse it; left unset, an engine is taken to have one,
    as it always was. Together they are what an engine registered without a
    database row needs to say about Excel, so core keeps no list of slugs.

    A worker can only honestly advertise an engine whose code it has, so the
    registration belongs in the engine package itself, run at import (see
    ``ADA_WORKER_PRELOAD``). An engine that stops being installed stops being
    offered when that worker's heartbeat goes stale, which is the point: the
    list reflects what can actually run right now.
    """
    for arg, value in (("export_entrypoint", export_entrypoint), ("import_entrypoint", import_entrypoint)):
        if value is not None and ":" not in value:
            raise ValueError(f"{arg} must be a dotted 'module:callable' path, got {value!r}")
    if supports_excel is False and (export_entrypoint or import_entrypoint):
        raise ValueError(f"engine {slug!r}: supports_excel=False contradicts an export/import entrypoint")
    spec: dict = {
        "slug": slug,
        "supports_grouping": bool(supports_grouping),
    }
    # Excel keys only when given: a spec without them means what it always did
    # (an Excel format through the siblings of ``entrypoint``).
    if supports_excel is not None:
        spec["supports_excel"] = bool(supports_excel)
    if export_entrypoint:
        spec["export_entrypoint"] = export_entrypoint
    if import_entrypoint:
        spec["import_entrypoint"] = import_entrypoint
    # Only a spec carrying BOTH a name and an entrypoint is offerable. Older
    # workers advertise flags alone, and a half-filled descriptor would surface
    # an engine the viewer cannot dispatch to -- worse than not offering it.
    if name and entrypoint:
        spec["name"] = name
        spec["description"] = description or ""
        spec["entrypoint"] = entrypoint
        if worker_capability:
            spec["worker_capability"] = worker_capability
    _ENGINE_CAPABILITY_REGISTRY[slug] = spec


def procedural_engine_specs() -> list[dict]:
    """The registered engine capability specs (a fresh copy so callers can't mutate
    the registry) — the shape the worker advertises in its heartbeat."""
    return [dict(v) for v in _ENGINE_CAPABILITY_REGISTRY.values()]


# The base adapy worker re-announces the built-in engines' capability flags (all
# non-grouping; ``echo`` without an Excel format), mirroring how blueprint_catalog
# re-announces the default engine's built-in blueprints. The engine-list endpoint also carries these statically, so
# the flags are correct with or without a live worker.
for _slug, _spec in BUILTIN_ENGINES.items():
    register_procedural_engine_capabilities(
        _slug,
        supports_grouping=bool(_spec.get("supports_grouping", False)),
        supports_excel=bool(_spec.get("supports_excel", True)),
    )
