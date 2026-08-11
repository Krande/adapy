"""Detailing-engine registry — a fabrication-detail stage advertised parallel to
the procedural (topology) engines.

A *detailing engine* adds connection details (joints, welds, bolts, gusset/base/
end plates) at member intersections, running AFTER the procedural compile has
produced the structural members. It mirrors the procedural-engine plumbing
1:1: a slug-keyed registry, built-ins registered at import, a ``*_specs()``
reader the worker folds into its heartbeat (``procedural_detailing_engine_specs``),
and a ``/procedural-models/detailing-engines`` endpoint that unions the static
built-ins with live-worker advertisements.

Two built-ins are registered at import:

- ``none`` — the sentinel meaning "no detailing" (structural-only GLB, the
  default; byte-identical to a compile that never selects detailing).
- ``adapy-default`` — the in-process builtin detailing engine
  (:func:`ada.topo_model.detailing.detail`) that adds the SteelStru starter
  joints (girder–girder gusset, beam–column end plate, column base plate).

An external capability engine (e.g. weld-gen) advertises ``inprocess=False`` and
a ``worker_capability`` by importing a module that calls
:func:`register_detailing_engine` at worker start — see ``ADA_WORKER_PRELOAD`` —
exactly like the procedural engine capabilities.
"""

from __future__ import annotations

__all__ = [
    "register_detailing_engine",
    "detailing_engine_specs",
    "DEFAULT_DETAILING",
]

#: The sentinel slug meaning "no detailing" (the default).
DEFAULT_DETAILING = "none"

# slug -> spec dict. Insertion-ordered; last writer per slug wins (a re-imported
# preload module just replaces its own entry). ``none`` is kept first so it is
# the natural default in a UI that lists these in order.
_DETAILING_REGISTRY: dict[str, dict] = {}


def register_detailing_engine(
    slug: str,
    name: str,
    *,
    description: str = "",
    inprocess: bool = False,
    entrypoint: str | None = None,
    worker_capability: str | None = None,
    joint_types: list[dict] | None = None,
) -> None:
    """Register (or replace) a detailing engine. ``slug`` is the engine selector
    (e.g. ``adapy-default``); ``inprocess`` marks a builtin that runs as stage 2
    of the same ``procedural_build`` job (Tier A) versus an external capability
    engine routed to its own pool (Tier B). ``entrypoint`` is the dotted
    ``module:callable`` with signature ``detail(assembly, options) -> assembly``
    (Tier A) / ``detail(artifact, options) -> bytes`` (Tier B). ``joint_types``
    is the advertised per-joint-type option spec list the UI is generated from.
    Idempotent by ``slug``."""
    _DETAILING_REGISTRY[slug] = {
        "slug": slug,
        "name": name,
        "description": description,
        "inprocess": bool(inprocess),
        "entrypoint": entrypoint,
        "worker_capability": worker_capability,
        "joint_types": [dict(j) for j in (joint_types or [])],
    }


def detailing_engine_specs() -> list[dict]:
    """The registered detailing-engine specs (fresh copies so callers can't mutate
    the registry) — the shape the worker advertises in its heartbeat."""
    out: list[dict] = []
    for v in _DETAILING_REGISTRY.values():
        spec = dict(v)
        spec["joint_types"] = [dict(j) for j in v.get("joint_types", [])]
        out.append(spec)
    return out


# ── built-ins registered at import ───────────────────────────────────

register_detailing_engine(
    DEFAULT_DETAILING,
    "none",
    description="No detailing — the compiled structural model is left as-is (default).",
    inprocess=True,
    entrypoint=None,
    worker_capability=None,
    joint_types=[],
)

register_detailing_engine(
    "adapy-default",
    "adapy detailing",
    description="Built-in adapy detailing: girder–girder gusset, beam–column end plate and column base-plate joints.",
    inprocess=True,
    entrypoint="ada.topo_model.detailing:detail",
    worker_capability=None,
    joint_types=[
        {
            "slug": "girder_gusset",
            "name": "Girder–girder gusset",
            "description": "Gusset plate + fillet weld beads at each I-girder to I-girder intersection.",
        },
        {
            "slug": "beam_column_endplate",
            "name": "Beam–column end plate",
            "description": "End plate + fillet weld (bolts metadata-first) where a girder frames into a column.",
        },
        {
            "slug": "column_base_plate",
            "name": "Column base plate",
            "description": "Base plate + fillet welds (anchor bolts metadata-first) at each column footing.",
        },
        {
            "slug": "box_to_box",
            "name": "Box-to-box clash cut",
            "description": "Boolean-cut the incoming box beam with the landing box member so they no longer clash (no weld/plate; opt-in).",
        },
    ],
)
