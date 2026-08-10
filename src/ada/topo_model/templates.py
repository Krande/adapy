"""Procedural start-from templates, announced by workers.

Each worker advertises the templates it can build in its registry heartbeat
(``procedural_template_specs``); the viewer's ``/procedural-templates`` endpoint
unions the *live* workers' announcements into the storage "New model from
template" dropdown, tagging each with the engine that offers it. So a template
appears exactly while a worker that can build it is up.

The base adapy worker announces the ``adapy-default`` templates defined below
(they compile with the in-repo engine, server-side or in-browser). A capability
worker (e.g. pm-engine) registers its own demos by importing a module that calls
:func:`register_procedural_template` at worker start — see ``ADA_WORKER_PRELOAD``.

A template's ``doc`` is the document committed verbatim when the user picks it;
for a non-default engine it is a thin routing document (``{"engine": ...,
"example": ...}``) the engine expands at compile time.
"""

from __future__ import annotations

from typing import Any

# slug -> {"slug", "name", "engine", "doc"}. Insertion-ordered so the dropdown
# keeps a stable, authored order.
_REGISTRY: dict[str, dict[str, Any]] = {}


def register_procedural_template(slug: str, name: str, engine: str, doc: dict) -> None:
    """Register (or replace) a start-from template. Idempotent by ``slug`` so a
    worker that re-imports its preload module doesn't duplicate entries."""
    _REGISTRY[slug] = {"slug": slug, "name": name, "engine": engine, "doc": doc}


def procedural_template_specs() -> list[dict]:
    """The registered templates, as the worker advertises them (a fresh copy so
    callers can't mutate the registry)."""
    return [
        {"slug": v["slug"], "name": v["name"], "engine": v["engine"], "doc": dict(v["doc"])}
        for v in _REGISTRY.values()
    ]


def _eq(
    name: str,
    description: str,
    x: float,
    y: float,
    z: float,
    lx: float,
    ly: float,
    lz: float,
    space: str,
    loc: str = "FLOOR",
    mass_dry: float = 1000.0,
    mass_cont: float = 0.0,
) -> dict:
    """A full TopoEquipment dict. The template doc is committed verbatim (not
    through the cellbuilder store, which would otherwise enrich a bare
    placement), so it must carry the fields TopoEquipment requires — SPACE_NAME,
    SPACE_LOC, COG and mass — up front. The catalog/archetype still supplies the
    render geometry from DESCRIPTION at compile time.

    ``x/y/z`` are WORLD coordinates (GLOBAL_COORDS=True): the compiler otherwise
    treats them as offsets from the SPACE_NAME cell's origin and adds the cell's
    Z on top, which double-counts (equipment ends up a floor/deck too high)."""
    return {
        "NAME": name,
        "DESCRIPTION": description,
        "X": x,
        "Y": y,
        "Z": z,
        "LX": lx,
        "LY": ly,
        "LZ": lz,
        "GLOBAL_COORDS": True,
        "SPACE_NAME": space,
        "SPACE_LOC": loc,
        "COGx": 0.0,
        "COGy": 0.0,
        "COGz": 0.0,
        "massDry": mass_dry,
        "massCont": mass_cont,
    }


def _localize(spaces: list[dict], equipments: list[dict]) -> list[dict]:
    """Rewrite equipment authored in world coords (via :func:`_eq`,
    ``GLOBAL_COORDS=True``) as PER-CELL LOCAL coords — offsets from the
    ``SPACE_NAME`` cell, ``GLOBAL_COORDS=False``. Identical world placement, but
    the equipment is now tied to its cell, so moving / recompiling the cell
    carries it (matching the cellbuilder's move-with-cell behaviour). Equipment
    whose space can't be resolved is left global. Mutates + returns the list."""
    by_name = {s["NAME"]: s for s in spaces}
    for e in equipments:
        s = by_name.get(e.get("SPACE_NAME"))
        if s is None:
            continue
        oz = float(s["Z"]) + (float(s["DZ"]) if e.get("SPACE_LOC") == "ROOF" else 0.0)
        e["X"] = round(float(e["X"]) - float(s["X"]), 6)
        e["Y"] = round(float(e["Y"]) - float(s["Y"]), 6)
        e["Z"] = round(float(e["Z"]) - oz, 6)
        e["GLOBAL_COORDS"] = False
    return equipments


def _steel_structure_demo_doc() -> dict:
    """A two-storey framed structure with a fully-enclosed HVAC room, routed
    process/electrical/duct services and two-ended site I/O."""
    spaces = [
        {"NAME": "Cell1", "INCLUDE": True, "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "INCLUDE": True, "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell3", "INCLUDE": True, "X": 0, "Y": 0, "Z": 3, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell4", "INCLUDE": True, "X": 5, "Y": 0, "Z": 3, "DX": 5, "DY": 5, "DZ": 3},
    ]
    equipments = _localize(
        spaces,
        [
            _eq("Pump2", "pump", 2, 2, 0, 1, 1, 1, "Cell1"),
            _eq("Tank2", "tank", 6.5, 1.5, 0, 2, 2, 2, "Cell2", mass_dry=2000, mass_cont=3000),
            _eq("SB2", "switchboard", 0.3, 2, 0, 0.8, 0.4, 1.2, "Cell1", mass_dry=500),
            _eq("Pump1", "pump", 2, 2, 3, 1, 1, 1, "Cell3"),
            _eq("Tank1", "tank", 6.5, 1.5, 3, 2, 2, 2, "Cell4", mass_dry=2000, mass_cont=3000),
            _eq("SB1", "switchboard", 0.3, 2, 3, 0.8, 0.4, 1.2, "Cell3", mass_dry=500),
            _eq("HVAC1", "hvac", 3, 3.5, 3, 1.5, 1, 1.2, "Cell3", mass_dry=800),
            _eq("Exhaust1", "exhaust_fan", 3, 3.5, 6, 0.8, 0.8, 0.6, "Cell3", loc="ROOF", mass_dry=300),
        ],
    )
    return {
        "grid": {},
        "blueprint": {"enclosed_cells": ["Cell3"]},
        "design_rules": "standard",
        "spaces": spaces,
        "equipments": equipments,
        "systems": [
            {"NAME": "CoolingWater", "TYPE": "piping", "MEDIUM": "water",
             "CONNECTIONS": [{"EQUIPMENT": "Pump1", "PORT": "discharge"}, {"EQUIPMENT": "Tank1", "PORT": "inlet"}]},
            {"NAME": "ServiceWater", "TYPE": "piping", "MEDIUM": "water",
             "CONNECTIONS": [{"EQUIPMENT": "Pump2", "PORT": "discharge"}, {"EQUIPMENT": "Tank2", "PORT": "inlet"}]},
            {"NAME": "Mains", "TYPE": "electrical",
             "CONNECTIONS": [{"SITE": "grid_supply", "POSITION": [0, 1, 1], "DIRECTION": "IN", "DIRECTION_VECTOR": [1, 0, 0]},
                             {"EQUIPMENT": "SB2", "PORT": "incoming"}]},
            {"NAME": "PowerFeed2", "TYPE": "electrical",
             "CONNECTIONS": [{"EQUIPMENT": "SB2", "PORT": "feeder"}, {"EQUIPMENT": "Pump2", "PORT": "power"}]},
            {"NAME": "DeckTie", "TYPE": "electrical",
             "CONNECTIONS": [{"EQUIPMENT": "SB2", "PORT": "feeder2"}, {"EQUIPMENT": "SB1", "PORT": "incoming"}]},
            {"NAME": "PowerFeed1", "TYPE": "electrical",
             "CONNECTIONS": [{"EQUIPMENT": "SB1", "PORT": "feeder"}, {"EQUIPMENT": "Pump1", "PORT": "power"}]},
            {"NAME": "HvacPower", "TYPE": "electrical",
             "CONNECTIONS": [{"EQUIPMENT": "SB1", "PORT": "feeder2"}, {"EQUIPMENT": "HVAC1", "PORT": "power"}]},
            {"NAME": "HvacExhaust", "TYPE": "duct", "MEDIUM": "air",
             "CONNECTIONS": [{"EQUIPMENT": "HVAC1", "PORT": "supply"}, {"EQUIPMENT": "Exhaust1", "PORT": "intake"}]},
            {"NAME": "Drain", "TYPE": "piping", "MEDIUM": "water",
             # Drain out the near (right, x=10) wall by Tank2 — the old x=0 site
             # was boxed in by switchboard SB2, so the run couldn't route.
             "CONNECTIONS": [{"EQUIPMENT": "Tank2", "PORT": "outlet"},
                             {"SITE": "drain", "POSITION": [10, 2.5, 1], "DIRECTION": "OUT", "DIRECTION_VECTOR": [1, 0, 0]}]},
            {"NAME": "Suction", "TYPE": "piping", "MEDIUM": "water",
             "CONNECTIONS": [{"SITE": "seawater", "POSITION": [0, 4, 1], "DIRECTION": "IN", "DIRECTION_VECTOR": [1, 0, 0]},
                             {"EQUIPMENT": "Pump2", "PORT": "suction"}]},
        ],
        "openings": [],
    }


def _topside_jacket_doc() -> dict:
    """A framed steel topside deck over an open tubular jacket truss — the
    SteelStru deck cells and a REPRESENTATION="JACKET" loft member built in one
    ProceduralBuilder pass."""
    _tj_spaces = [
        {"NAME": "DeckA", "INCLUDE": True, "X": -12, "Y": -12, "Z": 100, "DX": 12, "DY": 24, "DZ": 4},
        {"NAME": "DeckB", "INCLUDE": True, "X": 0, "Y": -12, "Z": 100, "DX": 12, "DY": 24, "DZ": 4},
        {"NAME": "DeckA2", "INCLUDE": True, "X": -12, "Y": -12, "Z": 104, "DX": 12, "DY": 24, "DZ": 4},
        {"NAME": "DeckB2", "INCLUDE": True, "X": 0, "Y": -12, "Z": 104, "DX": 12, "DY": 24, "DZ": 4},
    ]
    return {
        "grid": {},
        "blueprint": {},
        "design_rules": "standard",
        "spaces": _tj_spaces,
        "equipments": _localize(
            _tj_spaces,
            [
                # Authored in world coords on the deck floor at Z=100, centred on
                # DeckA (x -12..0) / DeckB (x 0..12); _localize ties each to its deck.
                _eq("Pump", "pump", -6, 0, 100, 1, 1, 1, "DeckA"),
                _eq("Tank", "tank", 6, 0, 100, 2, 2, 2, "DeckB", mass_dry=2000, mass_cont=3000),
            ],
        ),
        "systems": [
            {"NAME": "CoolingWater", "TYPE": "piping", "MEDIUM": "water",
             "CONNECTIONS": [{"EQUIPMENT": "Pump", "PORT": "discharge"}, {"EQUIPMENT": "Tank", "PORT": "inlet"}]},
        ],
        "openings": [],
        "loft_members": [
            {"NAME": "Jacket", "INCLUDE": True, "REPRESENTATION": "JACKET",
             "STATIONS": [
                 {"TYPE": "rectangle", "X": 0, "Y": 0, "Z": 0, "WIDTH": 40, "HEIGHT": 40, "SEGMENTS": 4},
                 {"TYPE": "rectangle", "X": 0, "Y": 0, "Z": 20, "WIDTH": 40, "HEIGHT": 40, "SEGMENTS": 4},
                 {"TYPE": "rectangle", "X": 0, "Y": 0, "Z": 60, "WIDTH": 31, "HEIGHT": 31, "SEGMENTS": 4},
                 {"TYPE": "rectangle", "X": 0, "Y": 0, "Z": 100, "WIDTH": 24, "HEIGHT": 24, "SEGMENTS": 4},
             ]},
        ],
    }


# The base adapy worker's built-in demos (adapy-default engine).
register_procedural_template("adapy:steel-demo", "Steel structure demo", "adapy-default", _steel_structure_demo_doc())
register_procedural_template("adapy:topside-jacket", "Topside + jacket", "adapy-default", _topside_jacket_doc())


def _register_loft_demos() -> None:
    """The loft demos (box/column/jacket/floater) as adapy-default templates.

    These are full ``loft_members`` documents — adapy's editable loft geometry
    representation (station stacks + placements), the loft analogue of box cells
    for spaces. The floater carries all eight members (four columns + four placed
    pontoons). They render as their loft surface (SURFACE_ONLY), stay fully
    editable in the cellbuilder, and compile with the built-in engine. The docs
    live as JSON alongside this module."""
    import json
    import pathlib

    data_dir = pathlib.Path(__file__).with_name("templates_data")
    for slug, name in (
        ("loft-box", "Loft box"),
        ("loft-column", "Loft column"),
        ("loft-jacket", "Loft jacket"),
        ("loft-floater", "Loft floater"),
    ):
        doc = json.loads((data_dir / f"{slug}.json").read_text())
        register_procedural_template(f"adapy:{slug}", name, "adapy-default", doc)


_register_loft_demos()


__all__ = ["register_procedural_template", "procedural_template_specs"]
