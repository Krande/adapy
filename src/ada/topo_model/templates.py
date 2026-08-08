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


def _steel_structure_demo_doc() -> dict:
    """A two-storey framed structure with a fully-enclosed HVAC room, routed
    process/electrical/duct services and two-ended site I/O."""
    return {
        "grid": {},
        "blueprint": {"enclosed_cells": ["Cell3"]},
        "design_rules": "standard",
        "spaces": [
            {"NAME": "Cell1", "INCLUDE": True, "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
            {"NAME": "Cell2", "INCLUDE": True, "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
            {"NAME": "Cell3", "INCLUDE": True, "X": 0, "Y": 0, "Z": 3, "DX": 5, "DY": 5, "DZ": 3},
            {"NAME": "Cell4", "INCLUDE": True, "X": 5, "Y": 0, "Z": 3, "DX": 5, "DY": 5, "DZ": 3},
        ],
        "equipments": [
            {"NAME": "Pump2", "DESCRIPTION": "pump", "X": 2, "Y": 2, "Z": 0, "LX": 1, "LY": 1, "LZ": 1},
            {"NAME": "Tank2", "DESCRIPTION": "tank", "X": 6.5, "Y": 1.5, "Z": 0, "LX": 2, "LY": 2, "LZ": 2},
            {"NAME": "SB2", "DESCRIPTION": "switchboard", "X": 0.3, "Y": 2, "Z": 0, "LX": 0.8, "LY": 0.4, "LZ": 1.2},
            {"NAME": "Pump1", "DESCRIPTION": "pump", "X": 2, "Y": 2, "Z": 3, "LX": 1, "LY": 1, "LZ": 1},
            {"NAME": "Tank1", "DESCRIPTION": "tank", "X": 6.5, "Y": 1.5, "Z": 3, "LX": 2, "LY": 2, "LZ": 2},
            {"NAME": "SB1", "DESCRIPTION": "switchboard", "X": 0.3, "Y": 2, "Z": 3, "LX": 0.8, "LY": 0.4, "LZ": 1.2},
            {"NAME": "HVAC1", "DESCRIPTION": "hvac", "X": 3, "Y": 3.5, "Z": 3, "LX": 1.5, "LY": 1, "LZ": 1.2},
            {"NAME": "Exhaust1", "DESCRIPTION": "exhaust_fan", "X": 3, "Y": 3.5, "Z": 6, "LX": 0.8, "LY": 0.8, "LZ": 0.6},
        ],
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
             "CONNECTIONS": [{"EQUIPMENT": "Tank2", "PORT": "outlet"},
                             {"SITE": "drain", "POSITION": [0, 2.5, 1], "DIRECTION": "OUT", "DIRECTION_VECTOR": [1, 0, 0]}]},
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
    return {
        "grid": {},
        "blueprint": {},
        "design_rules": "standard",
        "spaces": [
            {"NAME": "DeckA", "INCLUDE": True, "X": -12, "Y": -12, "Z": 100, "DX": 12, "DY": 24, "DZ": 4},
            {"NAME": "DeckB", "INCLUDE": True, "X": 0, "Y": -12, "Z": 100, "DX": 12, "DY": 24, "DZ": 4},
            {"NAME": "DeckA2", "INCLUDE": True, "X": -12, "Y": -12, "Z": 104, "DX": 12, "DY": 24, "DZ": 4},
            {"NAME": "DeckB2", "INCLUDE": True, "X": 0, "Y": -12, "Z": 104, "DX": 12, "DY": 24, "DZ": 4},
        ],
        "equipments": [
            {"NAME": "Pump", "DESCRIPTION": "pump", "X": -6, "Y": -1, "Z": 100, "LX": 1, "LY": 1, "LZ": 1},
            {"NAME": "Tank", "DESCRIPTION": "tank", "X": 3, "Y": -1, "Z": 100, "LX": 2, "LY": 2, "LZ": 2},
        ],
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


__all__ = ["register_procedural_template", "procedural_template_specs"]
