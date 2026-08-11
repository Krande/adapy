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
    "ADAPY_DEFAULT_JOINT_TYPES",
]

#: The sentinel slug meaning "no detailing" (the default).
DEFAULT_DETAILING = "none"

# The advertised per-joint-type OPTION SPEC the Detailing tab is generated from
# (Phase 3). Each joint type carries:
#   - ``slug`` / ``name`` / ``description`` — identity + label,
#   - ``default_enabled`` — whether the joint family fires unless the user toggles
#     it off (box-to-box is opt-in, the plated joints are default-on),
#   - ``fields`` — the per-joint numeric/enum knobs, each
#     ``{name, label, type: "number"|"bool"|"enum", default, min?, max?, options?,
#     unit?}``. LENGTH fields are advertised in MILLIMETRES (``unit: "mm"``);
#     ``ada.topo_model.detailing.detail`` converts mm -> m before it touches
#     geometry. The panel renders these verbatim — nothing joint-specific is
#     hardcoded frontend-side, so a new engine advertising different fields just
#     works.
# This literal is the single in-code source of truth; the slim REST fallback in
# ``ada.comms.rest.catalog`` mirrors it by value (that image must not import
# ``ada``), guarded by ``tests/comms/rest/test_procedural_detailing`` parity.
ADAPY_DEFAULT_JOINT_TYPES: list[dict] = [
    {
        "slug": "girder_gusset",
        "name": "Girder–girder gusset",
        "description": "Gusset plate + fillet weld beads at each I-girder to I-girder intersection.",
        "default_enabled": True,
        "fields": [
            {"name": "weld_leg", "label": "Weld leg", "type": "number", "default": 6.0, "min": 3.0, "max": 20.0, "unit": "mm"},
            {"name": "gusset_t", "label": "Gusset thickness", "type": "number", "default": 10.0, "min": 5.0, "max": 40.0, "unit": "mm"},
        ],
    },
    {
        "slug": "beam_column_endplate",
        "name": "Beam–column end plate",
        "description": "End plate + fillet weld (bolts metadata-first) where a girder frames into a column.",
        "default_enabled": True,
        "fields": [
            {"name": "plate_t", "label": "End-plate thickness", "type": "number", "default": 20.0, "min": 8.0, "max": 60.0, "unit": "mm"},
            {"name": "weld_leg", "label": "Weld leg", "type": "number", "default": 6.0, "min": 3.0, "max": 20.0, "unit": "mm"},
            {"name": "bolt", "label": "Bolt size", "type": "enum", "default": "M20", "options": ["M16", "M20", "M24", "M30"]},
        ],
    },
    {
        "slug": "column_base_plate",
        "name": "Column base plate",
        "description": "Base plate + fillet welds (anchor bolts metadata-first) at each column footing.",
        "default_enabled": True,
        "fields": [
            {"name": "overhang", "label": "Overhang", "type": "number", "default": 50.0, "min": 0.0, "max": 200.0, "unit": "mm"},
            {"name": "weld_leg", "label": "Weld leg", "type": "number", "default": 6.0, "min": 3.0, "max": 20.0, "unit": "mm"},
        ],
    },
    {
        "slug": "box_to_box",
        "name": "Box-to-box clash cut",
        "description": "Boolean-cut the incoming box beam with the landing box member so they no longer clash (no weld/plate; opt-in).",
        "default_enabled": False,
        "fields": [
            {"name": "clearance", "label": "Cut clearance", "type": "number", "default": 2.0, "min": 0.0, "max": 20.0, "unit": "mm"},
        ],
    },
]

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
    joint_types=ADAPY_DEFAULT_JOINT_TYPES,
)
