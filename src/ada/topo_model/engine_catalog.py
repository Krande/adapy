"""Per-engine *capability* flags, announced by workers.

A procedural engine may support features the built-in engines don't. The one
capability flagged here is ``supports_grouping``: whether the engine understands
the cellbuilder's cell GROUPS (a group is one structure compiled with its own
blueprint). The built-in engines do NOT — ``adapy-default`` compiles a single
model-level blueprint (and tolerates-but-ignores any ``groups``/``STRUCTURE_NAME``
a doc carries), ``echo`` renders raw boxes. A capability engine (e.g. pm-engine)
advertises ``supports_grouping=True`` by importing a module that calls
:func:`register_procedural_engine_capabilities` at worker start — see
``ADA_WORKER_PRELOAD`` — exactly like :func:`register_procedural_blueprint`.

Each worker advertises the registry in its heartbeat (``procedural_engine_specs``);
the viewer's ``/procedural-engines`` endpoint folds a live worker's flags onto the
matching engine summary by slug (built-ins carry their static flags directly), so
the frontend can gate the Groups UI on ``supports_grouping`` without hardcoding
any engine slug.
"""

from __future__ import annotations

from .engines import BUILTIN_ENGINES

__all__ = [
    "register_procedural_engine_capabilities",
    "procedural_engine_specs",
]

# engine slug -> {"slug", "supports_grouping"}. Insertion-ordered; last writer per
# slug wins (a re-imported preload module just replaces its own entry).
_ENGINE_CAPABILITY_REGISTRY: dict[str, dict] = {}


def register_procedural_engine_capabilities(
    slug: str,
    *,
    supports_grouping: bool = False,
) -> None:
    """Register (or replace) the capability flags an engine advertises. ``slug`` is
    the engine slug (e.g. ``pm-engine``); ``supports_grouping`` advertises whether
    the engine compiles per-group blueprints. Idempotent by ``slug``."""
    _ENGINE_CAPABILITY_REGISTRY[slug] = {
        "slug": slug,
        "supports_grouping": bool(supports_grouping),
    }


def procedural_engine_specs() -> list[dict]:
    """The registered engine capability specs (a fresh copy so callers can't mutate
    the registry) — the shape the worker advertises in its heartbeat."""
    return [dict(v) for v in _ENGINE_CAPABILITY_REGISTRY.values()]


# The base adapy worker re-announces the built-in engines' capability flags (all
# non-grouping), mirroring how blueprint_catalog re-announces the default engine's
# built-in blueprints. The engine-list endpoint also carries these statically, so
# the flags are correct with or without a live worker.
for _slug, _spec in BUILTIN_ENGINES.items():
    register_procedural_engine_capabilities(_slug, supports_grouping=bool(_spec.get("supports_grouping", False)))
