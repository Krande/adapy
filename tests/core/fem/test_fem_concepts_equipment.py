"""The viewer's FEM concepts for a concept model: equipment boxes, the loads an analysis model
generated from them, the concept supports, and the native GLB writer carrying all of it."""

from __future__ import annotations

import json
import shutil
import struct

import pytest

import ada
from ada.cadit.ngeom.export import _add_design_extension
from ada.extension.fem_concepts_builder import build_design_fem_concepts
from ada.fem import Bc, FemSet
from ada.fem.concept.constraints import ConstraintConceptDofType, ConstraintConceptPoint


@pytest.fixture
def segments_part(example_files, tmp_path):
    # Read a copy: the reader writes a .sat beside the XML it is given.
    src = example_files / "fem_files" / "sesam" / "genie_loads_segments.xml"
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    (part,) = ada.from_genie_xml(dst).get_all_subparts()
    return part


def test_placed_equipment_is_a_box_and_its_resolved_load_is_marked_as_generated(segments_part):
    fc = build_design_fem_concepts(segments_part)

    (box,) = fc.equipment
    assert (box.name, box.mass, box.load_cases) == ("Eq1", 1000.0, ["LC4"])
    assert [v.root for v in (box.origin, box.box_min, box.box_max, box.cog)] == [
        [2.0, 10.0, 0.0],
        [-0.5, -0.5, 0.0],
        [0.5, 0.5, 1.0],
        [2.0, 10.0, 0.5],
    ]

    (lc4,) = [s for s in fc.scenarios if s.name == "LC4"]
    (load,) = lc4.loads
    assert (load.type.value, load.generated_by, load.end_magnitude) == ("line", "Eq1", None)
    assert load.magnitude == pytest.approx(9806.65)
    # every other scenario holds only the loads a user applied
    assert all(not ld.generated_by for s in fc.scenarios if s.name != "LC4" for ld in s.loads)


def test_concept_supports_are_drawn_until_a_mesh_carries_its_own():
    part = ada.Part("P")
    part.concept_fem.constraints.add_point_constraint(
        ConstraintConceptPoint("S1", (1, 2, 3), ConstraintConceptDofType.pinned())
    )
    (bc,) = build_design_fem_concepts(part).bcs
    assert (bc.name, bc.dofs, bc.bc_type, [p.root for p in bc.positions]) == ("S1", [1, 2, 3], "fixed", [[1, 2, 3]])

    # A meshed model's BCs are the simulation extension's to draw; drawing both doubles the glyph.
    n = part.fem.nodes.add(ada.Node((1, 2, 3)))
    part.fem.add_bc(Bc("fem_S1", FemSet("s1", [n], FemSet.TYPES.NSET), [1, 2, 3]))
    assert build_design_fem_concepts(part) is None


def _glb(doc: dict, binary: bytes) -> bytes:
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(body) + 8 + len(binary)
    return (
        b"glTF"
        + struct.pack("<II", 2, total)
        + struct.pack("<I4s", len(body), b"JSON")
        + body
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def test_a_native_glb_gets_the_design_block_and_keeps_its_geometry(segments_part, tmp_path):
    path = tmp_path / "native.glb"
    geometry = bytes(range(64))
    doc = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "root"}, {"name": "faces", "mesh": 0}],
        "extensions": {"ADA_EXT_data": {"design_objects": [], "simulation_objects": [], "version": "1"}},
    }
    path.write_bytes(_glb(doc, geometry))

    _add_design_extension(segments_part, path)

    data = path.read_bytes()
    (total,) = struct.unpack_from("<I", data, 8)
    (json_len,) = struct.unpack_from("<I", data, 12)
    assert total == len(data) and json_len % 4 == 0
    out = json.loads(data[20 : 20 + json_len])
    (design,) = out["extensions"]["ADA_EXT_data"]["design_objects"]
    assert design["node_references"]["faces"] == ["faces"]
    assert [e["name"] for e in design["fem_concepts"]["equipment"]] == ["Eq1"]
    assert "ADA_EXT_data" in out["extensionsUsed"]
    assert data[20 + json_len + 8 : 20 + json_len + 8 + len(geometry)] == geometry
