"""Routing-quality guards: penetrations only through built walls, and runs never
laid in a floor plate."""

from __future__ import annotations

import ada
from ada.topo_model.blueprint import SteelStru
from ada.topo_model.compile import (
    _blueprint_options,
    _build_systems,
    _equipment_to_object,
    _routing_grid,
    _space_to_box,
)
from ada.topology import TopologyBuilder
from ada.topology.entities import TopoEquipment, TopoSpace


def _eq(name, desc, space, x, y, z, lx, ly, lz):
    return {
        "NAME": name,
        "DESCRIPTION": desc,
        "SPACE_NAME": space,
        "SPACE_LOC": "FLOOR",
        # x/y/z here are WORLD coords (e.g. Tank1 at x=6.5 sits in Cell2's 5..10
        # span), so opt out of the default cell-relative placement.
        "GLOBAL_COORDS": True,
        "X": x,
        "Y": y,
        "Z": z,
        "LX": lx,
        "LY": ly,
        "LZ": lz,
        "COGx": 0,
        "COGy": 0,
        "COGz": lz / 2,
        "massDry": 100,
        "massCont": 0,
    }


def _compile_systems(doc):
    spaces = [TopoSpace(**s) for s in doc["spaces"]]
    boxes = [_space_to_box(s) for s in spaces]
    builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru(**_blueprint_options(doc)))
    builder.build()
    eqs = [_equipment_to_object(TopoEquipment(**e)) for e in doc["equipments"]]
    emap = {o.name: o for o in eqs if isinstance(o, ada.Equipment)}
    return _build_systems(doc, emap, spaces, builder.cell_graph)


def _sleeves(parts):
    return [
        s for p in parts if p.name == "Penetrations" for s in p.get_all_physical_objects() if s.name.endswith("_sleeve")
    ]


def test_run_across_unbuilt_boundary_has_no_penetration():
    # Two open cells sharing an (unbuilt) boundary at x=5; a pump->tank run crosses
    # it. With no wall actually built there, the run must NOT get a sleeve/hole.
    doc = {
        "spaces": [
            {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
            {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        ],
        "equipments": [
            _eq("Pump1", "pump", "Cell1", 2, 2, 0, 1, 1, 1),
            _eq("Tank1", "tank", "Cell2", 6.5, 1.5, 0, 2, 2, 2),
        ],
        "systems": [
            {
                "NAME": "CW",
                "TYPE": "piping",
                "MEDIUM": "water",
                "CONNECTIONS": [
                    {"EQUIPMENT": "Pump1", "PORT": "discharge"},
                    {"EQUIPMENT": "Tank1", "PORT": "inlet"},
                ],
            }
        ],
    }
    assert _sleeves(_compile_systems(doc)) == []


def test_run_across_enclosed_wall_is_penetrated():
    # The same two cells but Cell1 is fully enclosed → its shared wall at x=5 is
    # plated, so the crossing run gets exactly one sleeve.
    doc = {
        "blueprint": {"enclosed_cells": ["Cell1"]},
        "spaces": [
            {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
            {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        ],
        "equipments": [
            _eq("Pump1", "pump", "Cell1", 2, 2, 0, 1, 1, 1),
            _eq("Tank1", "tank", "Cell2", 6.5, 1.5, 0, 2, 2, 2),
        ],
        "systems": [
            {
                "NAME": "CW",
                "TYPE": "piping",
                "MEDIUM": "water",
                "CONNECTIONS": [
                    {"EQUIPMENT": "Pump1", "PORT": "discharge"},
                    {"EQUIPMENT": "Tank1", "PORT": "inlet"},
                ],
            }
        ],
    }
    assert len(_sleeves(_compile_systems(doc))) == 1


def test_routing_grid_avoids_deck_planes():
    # Two stacked cells → deck planes at z = 0, 3, 6. The routing lattice must not
    # place a level on any deck plane (so a horizontal run never lies in a floor
    # plate), and must extend below the lowest deck (a sub-floor void for drains).
    spaces = [
        TopoSpace(NAME="C1", X=0, Y=0, Z=0, DX=5, DY=5, DZ=3),
        TopoSpace(NAME="C2", X=0, Y=0, Z=3, DX=5, DY=5, DZ=3),
    ]
    grid = _routing_grid(spaces, [])
    for deck in (0.0, 3.0, 6.0):
        assert all(abs(z - deck) > 1e-6 for z in grid.z_list), f"deck plane {deck} left in the lattice"
    assert min(grid.z_list) < 0.0  # sub-floor band below the lowest deck
