"""Procedural cell-type and opening-type catalogs, announced by workers.

The cellbuilder's ``+ Cell`` and ``+ Opening`` buttons place a box seeded from a
*type* — a named blueprint carrying a default size (and, for a cell, extra entity
metadata; for an opening, the door/window subtype that drives its reinforcement
framing). Rather than hardcode those defaults in the frontend, each worker
advertises the cell/opening types it can build in its registry heartbeat
(``procedural_cell_specs`` / ``procedural_opening_specs``); the viewer's
``/procedural-models/cell-types`` and ``.../opening-types`` endpoints union the
*live* workers' announcements with a static built-in set (defined in the slim
``ada.comms.rest.catalog`` so the dropdowns are never empty without a worker).

The base adapy worker announces the ``adapy-default`` built-ins registered below.
A capability worker (e.g. pm-engine) registers its own by importing a module that
calls :func:`register_procedural_cell_type` / :func:`register_procedural_opening_type`
at worker start — see ``ADA_WORKER_PRELOAD`` — exactly like
:func:`ada.topo_model.register_procedural_template`.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "register_procedural_cell_type",
    "register_procedural_opening_type",
    "procedural_cell_type_specs",
    "procedural_opening_type_specs",
]

# slug -> {"slug", "name", "description", "size", "metadata"} (cells) and
# slug -> {"slug", "name", "description", "subtype", "size"} (openings).
# Insertion-ordered so the dropdown keeps a stable, authored order.
_CELL_REGISTRY: dict[str, dict] = {}
_OPENING_REGISTRY: dict[str, dict] = {}


def register_procedural_cell_type(
    slug: str,
    name: str,
    size: tuple[float, float, float],
    *,
    description: str = "",
    metadata: dict | None = None,
) -> None:
    """Register (or replace) a space-cell type. ``size`` is the default box extent
    ``(DX, DY, DZ)`` a freshly-placed cell is seeded with; ``metadata`` is extra
    ``TopoSpace`` entity fields round-tripped onto the placed cell. Idempotent by
    ``slug`` so a worker that re-imports its preload module doesn't duplicate
    entries."""
    _CELL_REGISTRY[slug] = {
        "slug": slug,
        "name": name,
        "description": description,
        "size": [float(size[0]), float(size[1]), float(size[2])],
        "metadata": dict(metadata) if metadata else {},
    }


def register_procedural_opening_type(
    slug: str,
    name: str,
    subtype: Literal["door", "window"],
    size: tuple[float, float, float],
    *,
    description: str = "",
) -> None:
    """Register (or replace) an opening type. ``subtype`` (``door``/``window``)
    drives the reinforcement framing the compiler frames around the hole; ``size``
    is the default box extent ``(DX, DY, DZ)`` a freshly-placed opening is seeded
    with. Idempotent by ``slug``."""
    if subtype not in ("door", "window"):
        raise ValueError(f"opening subtype must be 'door' or 'window', got {subtype!r}")
    _OPENING_REGISTRY[slug] = {
        "slug": slug,
        "name": name,
        "description": description,
        "subtype": subtype,
        "size": [float(size[0]), float(size[1]), float(size[2])],
    }


def procedural_cell_type_specs() -> list[dict]:
    """The registered cell types, as the worker advertises them (a fresh copy so
    callers can't mutate the registry)."""
    return [
        {
            "slug": v["slug"],
            "name": v["name"],
            "description": v["description"],
            "size": list(v["size"]),
            "metadata": dict(v["metadata"]),
        }
        for v in _CELL_REGISTRY.values()
    ]


def procedural_opening_type_specs() -> list[dict]:
    """The registered opening types, as the worker advertises them (a fresh copy
    so callers can't mutate the registry)."""
    return [
        {
            "slug": v["slug"],
            "name": v["name"],
            "description": v["description"],
            "subtype": v["subtype"],
            "size": list(v["size"]),
        }
        for v in _OPENING_REGISTRY.values()
    ]


# The base adapy worker's built-in cell/opening types (adapy-default engine). The
# same defaults are mirrored statically in ``ada.comms.rest.catalog`` so the slim
# API can offer them WITHOUT a live worker; a live worker re-announces them here
# (deduped by slug in the endpoint) alongside any engine-registered extras.
register_procedural_cell_type(
    "room",
    "Room",
    (5.0, 5.0, 3.0),
    description="A generic rectangular space cell (5 x 5 x 3 m).",
)
register_procedural_opening_type(
    "door",
    "Door",
    "door",
    (0.9, 0.9, 2.1),
    description="A full-height doorway cut to the floor (jambs + lintel + threshold).",
)
register_procedural_opening_type(
    "window",
    "Window",
    "window",
    (1.2, 1.2, 1.0),
    description="A punched window opening at its placed height (jambs + head + sill).",
)
