"""Negative-volume openings (door/window) cut built plates and add framing.

An enclosed cell plates its four walls; a door opening cuts one wall to the floor
and frames it with jamb studs + a lintel + a threshold, while a window opening
cuts a punched rectangle in another wall and frames it with jamb studs + a head +
a sill. A doc with no openings compiles exactly as before."""

from __future__ import annotations

import ada
from ada.topo_model.blueprint import SteelStru
from ada.topo_model.compile import (
    _apply_openings,
    _space_to_box,
    compile_procedural_doc,
)
from ada.topology import TopologyBuilder
from ada.topology.entities import TopoOpening, TopoSpace

SPACES = [
    {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
]

DOOR = {
    "NAME": "Door1",
    "SUBTYPE": "door",
    "USE_GLOBAL_COORDS": True,
    "X": -0.2,
    "Y": 1.0,
    "Z": 0.0,
    "DX": 0.4,
    "DY": 1.0,
    "DZ": 2.1,
}
WINDOW = {
    "NAME": "Win1",
    "SUBTYPE": "window",
    "USE_GLOBAL_COORDS": True,
    "X": 2.0,
    "Y": -0.2,
    "Z": 1.0,
    "DX": 1.0,
    "DY": 0.4,
    "DZ": 1.0,
}


def _enclosed_assembly():
    spaces = [TopoSpace(**s) for s in SPACES]
    bp = SteelStru(enclosed_cells=["Cell1"])
    builder = TopologyBuilder.from_prim_boxes([_space_to_box(s) for s in spaces], blueprint=bp)
    builder.build()
    a = builder.get_output_assembly("M")
    return bp, a, spaces


def test_subtype_defaults_to_door():
    assert TopoOpening(NAME="o", USE_GLOBAL_COORDS=True, X=0, Y=0, Z=0, DX=1, DY=1, DZ=1).SUBTYPE == "door"


def test_openings_cut_the_walls_they_overlap():
    bp, a, spaces = _enclosed_assembly()
    _apply_openings(bp, a, spaces, [DOOR, WINDOW])

    cut = {
        p.name: [b.primitive.name for b in p.booleans]
        for p in a.get_all_physical_objects(by_type=ada.Plate)
        if p.booleans
    }
    # exactly two wall plates were cut, one per opening
    assert sum(len(v) for v in cut.values()) == 2
    all_cuts = {name for names in cut.values() for name in names}
    assert all_cuts == {"Door1_cut", "Win1_cut"}
    # only wall plates (not floors) were cut
    assert all(name.startswith("Wall_") for name in cut)


def test_door_reinforcement_members():
    bp, a, spaces = _enclosed_assembly()
    _apply_openings(bp, a, spaces, [DOOR])
    beams = {b.name for b in a.get_all_physical_objects(by_type=ada.Beam)}
    assert {
        "Opening_Door1_jamb_L",
        "Opening_Door1_jamb_R",
        "Opening_Door1_lintel",
        "Opening_Door1_threshold",
    } <= beams
    # a door reaches the floor: its threshold sits at Z=0 (the wall base)
    threshold = next(b for b in a.get_all_physical_objects(by_type=ada.Beam) if b.name.endswith("threshold"))
    assert abs(float(threshold.n1.p[2])) < 1e-6 and abs(float(threshold.n2.p[2])) < 1e-6
    # framing reuses the wall stiffener/stud section
    assert threshold.section.name == bp.stringer_sec


def test_window_reinforcement_members():
    bp, a, spaces = _enclosed_assembly()
    _apply_openings(bp, a, spaces, [WINDOW])
    beams = {b.name for b in a.get_all_physical_objects(by_type=ada.Beam)}
    assert {
        "Opening_Win1_jamb_L",
        "Opening_Win1_jamb_R",
        "Opening_Win1_head",
        "Opening_Win1_sill",
    } <= beams
    # a window is a punched rectangle at its placed Z: its sill sits at Z=1, not the floor
    sill = next(b for b in a.get_all_physical_objects(by_type=ada.Beam) if b.name.endswith("_sill"))
    assert abs(float(sill.n1.p[2]) - 1.0) < 1e-6


def test_compile_with_openings_grows_and_is_glb():
    doc = {"blueprint": {"enclosed_cells": ["Cell1"]}, "spaces": SPACES, "openings": [DOOR, WINDOW]}
    glb = compile_procedural_doc(doc, blueprint_name="steel_stru")
    assert glb[:4] == b"glTF"
    base = compile_procedural_doc({k: v for k, v in doc.items() if k != "openings"}, blueprint_name="steel_stru")
    # the reinforcement framing makes the openings compile larger than the plain one
    assert len(glb) > len(base)


def test_empty_openings_is_backward_compatible():
    doc = {"blueprint": {"enclosed_cells": ["Cell1"]}, "spaces": SPACES}
    with_empty = compile_procedural_doc({**doc, "openings": []}, blueprint_name="steel_stru")
    without = compile_procedural_doc(doc, blueprint_name="steel_stru")
    # an empty openings list adds no geometry -> same GLB size (byte-for-byte the
    # export embeds a fresh id, so compare length rather than exact bytes)
    assert with_empty[:4] == b"glTF" and len(with_empty) == len(without)


def test_opening_overlapping_no_plate_is_noop():
    # blueprint_name="none" builds no walls -> the opening cuts nothing, no error
    doc = {"spaces": SPACES, "openings": [DOOR]}
    glb = compile_procedural_doc(doc, blueprint_name="none")
    assert glb[:4] == b"glTF"
