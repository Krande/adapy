"""Per-engine procedural *blueprint* catalog, announced by workers.

A procedural engine compiles the document's space cells through a named
*blueprint* — the structural interpretation that turns raw boxes into geometry.
The ``adapy-default`` engine ships two: ``steel_stru`` (a decked steel structure)
and ``none`` (the raw space boxes, no structure). Rather than hardcode that list
in the frontend, each worker advertises the blueprints it can build in its
registry heartbeat (``procedural_blueprint_specs``, engine-scoped); the viewer's
``/procedural-models/blueprints?engine=<slug>`` endpoint unions the *live*
workers' announcements with a static built-in set (defined in the slim
``ada.comms.rest.catalog`` so the dropdown is never empty without a worker).

The blueprint list is advertised PER ENGINE: switching the compile engine
switches the offered blueprints. The base adapy worker announces the
``adapy-default`` built-ins registered below; a capability worker (e.g. a
topology engine) registers its own by importing a module that calls
:func:`register_procedural_blueprint` at worker start — see ``ADA_WORKER_PRELOAD``
— exactly like :func:`ada.topo_model.register_procedural_cell_type`.
"""

from __future__ import annotations

from .engines import DEFAULT_ENGINE_SLUG

__all__ = [
    "register_procedural_blueprint",
    "procedural_blueprint_specs",
]

# engine slug -> {blueprint slug -> {"engine", "slug", "name", "description"}}.
# Nested-and-insertion-ordered so each engine's dropdown keeps a stable, authored
# order (the FIRST entry is the engine's default blueprint).
_BLUEPRINT_REGISTRY: dict[str, dict[str, dict]] = {}


def register_procedural_blueprint(
    engine: str,
    slug: str,
    name: str,
    *,
    description: str = "",
) -> None:
    """Register (or replace) a structural blueprint offered by ``engine``. The
    ``slug`` is the ``blueprint_name`` the compiler dispatches on (e.g.
    ``steel_stru``/``none`` for the default engine); ``name`` is the dropdown
    label. Idempotent by ``(engine, slug)`` so a worker that re-imports its
    preload module doesn't duplicate entries. The first blueprint registered for
    an engine is that engine's default."""
    _BLUEPRINT_REGISTRY.setdefault(engine, {})[slug] = {
        "engine": engine,
        "slug": slug,
        "name": name,
        "description": description,
    }


def procedural_blueprint_specs(engine: str | None = None) -> list[dict]:
    """The registered blueprint specs (a fresh copy so callers can't mutate the
    registry). With ``engine`` given, only that engine's blueprints; with
    ``engine=None``, the union across every registered engine (each spec carries
    its ``engine`` so the endpoint can filter) — the shape the worker advertises."""
    if engine is not None:
        return [dict(v) for v in _BLUEPRINT_REGISTRY.get(engine, {}).values()]
    return [dict(v) for blueprints in _BLUEPRINT_REGISTRY.values() for v in blueprints.values()]


# The base adapy worker's built-in blueprints (adapy-default engine). The same
# defaults are mirrored statically in ``ada.comms.rest.catalog`` so the slim API
# can offer them WITHOUT a live worker; a live worker re-announces them here
# (deduped by slug in the endpoint) alongside any engine-registered extras.
register_procedural_blueprint(
    DEFAULT_ENGINE_SLUG,
    "steel_stru",
    "Steel structure",
    description="Decked steel structure framed over the space cells (girders, stringers, plate decks and walls).",
)
register_procedural_blueprint(
    DEFAULT_ENGINE_SLUG,
    "none",
    "Raw boxes — no blueprint",
    description="Render the space cells as raw boxes with no structural blueprint.",
)
