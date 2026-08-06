"""ProceduralBuilder: the root object that owns a procedural cell-model compile.

Exercises the documented usage patterns — one-shot compile, phase-by-phase
driving, the ``.procedural`` root back-reference reachable from the blueprint and
from any GraphFace, LOD living on the root, and equivalence with the functional
``compile_procedural_doc`` wrapper.
"""

from __future__ import annotations

import ada  # noqa: F401  (kept for parity with sibling tests / interactive use)
import pytest

from ada.topo_model import ProceduralBuilder
from ada.topo_model.compile import compile_procedural_doc

DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
    "equipments": [
        {
            "NAME": "Pump2",
            "DESCRIPTION": "pump",
            "SPACE_NAME": "Cell1",
            "SPACE_LOC": "FLOOR",
            "X": 2.0, "Y": 2.0, "Z": 0.0, "LX": 1.0, "LY": 1.0, "LZ": 1.0,
            "COGx": 0, "COGy": 0, "COGz": 0.5, "massDry": 1000, "massCont": 0,
        },
        {
            "NAME": "Tank2",
            "DESCRIPTION": "tank",
            "SPACE_NAME": "Cell2",
            "SPACE_LOC": "FLOOR",
            "X": 6.5, "Y": 1.5, "Z": 0.0, "LX": 2.0, "LY": 2.0, "LZ": 2.0,
            "COGx": 0, "COGy": 0, "COGz": 1.0, "massDry": 1000, "massCont": 0,
        },
    ],
    "systems": [
        {
            "NAME": "ServiceWater",
            "TYPE": "piping",
            "CONNECTIONS": [
                {"EQUIPMENT": "Pump2", "PORT": "discharge"},
                {"EQUIPMENT": "Tank2", "PORT": "inlet"},
            ],
        }
    ],
}


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


# --- one-shot happy path --------------------------------------------------- #
def test_compile_returns_glb():
    """The documented one-liner: build a root, compile, get GLB bytes."""
    glb = ProceduralBuilder(DOC).compile()
    assert _is_glb(glb) and len(glb) > 500


def test_wrapper_matches_builder():
    """``compile_procedural_doc`` is a thin wrapper over ProceduralBuilder — for
    the same document both must yield the same model. (Object GUIDs are freshly
    generated per build, so the GLB is compared by size, not byte-for-byte: even
    two ProceduralBuilder builds of one doc differ only in embedded GUIDs.)"""
    from_wrapper = compile_procedural_doc(DOC)
    from_builder = ProceduralBuilder(DOC).compile()
    assert _is_glb(from_wrapper) and _is_glb(from_builder)
    assert len(from_wrapper) == len(from_builder)


def test_no_spaces_raises():
    with pytest.raises(ValueError, match="no spaces"):
        ProceduralBuilder({"spaces": []})


# --- phase-by-phase driving + owned state ---------------------------------- #
def test_phases_populate_owned_state():
    """Driving the phases individually fills the builder's owned state: topology,
    blueprint, assembly, the equipment map and the system parts."""
    pb = ProceduralBuilder(DOC)
    assert pb.blueprint is None and pb.assembly is None and pb.cell_graph is None

    pb.build_structure()
    assert pb.blueprint is not None
    assert pb.assembly is not None
    assert pb.cell_graph is not None  # steel_stru builds a cell graph

    pb.build_equipment()
    assert set(pb.equipment_map) == {"Pump2", "Tank2"}

    pb.build_systems()
    assert any(p.name == "Systems" for p in pb.systems)

    glb = pb.to_glb()
    assert _is_glb(glb)


# --- the .procedural root back-reference ----------------------------------- #
def test_blueprint_reaches_root():
    """The blueprint carries a ``.procedural`` back-reference to its owning root."""
    pb = ProceduralBuilder(DOC)
    pb.build_structure()
    assert pb.blueprint.procedural is pb


def test_graphface_reaches_root():
    """Any GraphFace reaches the root through
    ``face.parent_cell.cell_graph.procedural`` — chaining up cell -> graph -> root."""
    pb = ProceduralBuilder(DOC)
    pb.build_structure()
    faces = pb.cell_graph.get_external_floors()
    assert faces, "expected at least one classified floor face"
    for face in faces:
        assert face.parent_cell.cell_graph.procedural is pb


# --- LOD lives on the root ------------------------------------------------- #
def test_detail_flag_lives_on_root():
    """The LOD has a single home on the root; the blueprint reads its own detail
    flag from ``self.procedural`` once attached."""
    sim = ProceduralBuilder(DOC, lod="sim")
    detail = ProceduralBuilder(DOC, lod="detail")
    assert sim.detail is False and detail.detail is True

    sim.build_structure()
    detail.build_structure()
    assert sim.blueprint.detail is False
    assert detail.blueprint.detail is True


def test_detail_compile_differs_from_sim():
    """The detail model adds trimmed decks + I-girder joints, so its GLB differs
    from the simulation model's."""
    sim = ProceduralBuilder(DOC, lod="sim").compile()
    detail = ProceduralBuilder(DOC, lod="detail").compile()
    assert _is_glb(sim) and _is_glb(detail)
    assert sim != detail


# --- blueprint_name="none" ------------------------------------------------- #
def test_none_blueprint_skips_topology():
    """``blueprint_name='none'`` renders the raw space boxes and builds no
    topology (``cell_graph`` stays None)."""
    pb = ProceduralBuilder(DOC, blueprint_name="none")
    pb.build_structure()
    assert pb.cell_graph is None
    assert pb.assembly is not None
    glb = ProceduralBuilder(DOC, blueprint_name="none").compile()
    assert _is_glb(glb)
