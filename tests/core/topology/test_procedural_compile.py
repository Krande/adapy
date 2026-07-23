"""compile_procedural_doc: procedural cell-model doc -> GLB bytes."""

from __future__ import annotations

import pytest

from ada.topo_model.compile import compile_procedural_doc

DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
    "equipments": [
        {
            "NAME": "P1",
            "SPACE_NAME": "Cell1",
            "SPACE_LOC": "ROOF",
            "DESCRIPTION": "pump",
            "X": 2.0,
            "Y": 2.0,
            "Z": 3.0,
            "LX": 1.0,
            "LY": 1.0,
            "LZ": 1.0,
            "COGx": 0,
            "COGy": 0,
            "COGz": 0.5,
            "massDry": 1000,
            "massCont": 0,
        }
    ],
}


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


def test_compile_raw_boxes():
    glb = compile_procedural_doc(DOC, blueprint_name="none")
    assert _is_glb(glb) and len(glb) > 500


def test_compile_steel_stru():
    glb = compile_procedural_doc(DOC, blueprint_name="steel_stru")
    assert _is_glb(glb)
    # the structural compile carries plates+beams and is substantially larger
    assert len(glb) > len(compile_procedural_doc(DOC, blueprint_name="none"))


def test_compile_empty_doc_raises():
    with pytest.raises(ValueError, match="no spaces"):
        compile_procedural_doc({"spaces": []})


def test_compile_missing_coords_raises():
    with pytest.raises(ValueError, match="missing coordinates"):
        compile_procedural_doc({"spaces": [{"NAME": "Cell1"}]}, blueprint_name="none")
