"""Fully-native adacpp STEP -> GLB pipeline (ada.cadit.step.native_step_to_glb).

A single adacpp C++ entry point does the whole conversion in-process (Part-21 reader + thread-pool
libtess2 tessellation + merge-by-colour GLB writer), replacing the Python streaming reader + worker
pool. v1 renders a valid GLB (merge-by-colour materials) but does NOT yet carry the ADA_EXT picking
sidecar — so this asserts a renderable GLB, not the per-part draw_ranges contract.

Gated on the adacpp native entry point being importable. All fixtures are synthetic — no client data.
"""

import json
import struct

import pytest

import ada
from ada.cadit.step.native_step_to_glb import native_adacpp_available, native_step_to_glb

pytestmark = pytest.mark.skipif(not native_adacpp_available(), reason="adacpp native STEP->GLB not available")


def _glb_json(glb_path) -> dict:
    raw = glb_path.read_bytes()
    assert raw[:4] == b"glTF", "GLB magic"
    assert struct.unpack("<I", raw[4:8])[0] == 2, "glTF version 2"
    assert struct.unpack("<I", raw[8:12])[0] == len(raw), "total length == file size"
    jlen = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20 : 20 + jlen])


def test_native_step_to_glb_renders_merge_by_colour(tmp_path):
    # Two differently-coloured solids -> two merge-by-colour materials.
    from ada.visit.colors import Color

    a = ada.PrimBox("a", (0, 0, 0), (1, 1, 1))
    a.color = Color(1, 0, 0)
    b = ada.PrimBox("b", (2, 0, 0), (3, 1, 1))
    b.color = Color(0, 0, 1)
    src = tmp_path / "synthetic.step"
    (ada.Assembly("m") / (ada.Part("p") / [a, b])).to_stp(src)

    out = tmp_path / "out.glb"
    stats = native_step_to_glb(src, out, deflection=1.0)

    assert stats["solids"] >= 2 and stats["skipped"] == 0
    gltf = _glb_json(out)
    assert gltf["meshes"], "has meshes"
    assert len(gltf["materials"]) == 2, "two colours -> two merge-by-colour materials"
    # every mesh primitive must have POSITION + indices (a real, drawable mesh)
    accessors = gltf["accessors"]
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            assert "POSITION" in prim["attributes"]
            assert accessors[prim["indices"]]["count"] > 0


@pytest.mark.xfail(reason="native tessellator drops full/periodic (360deg) cylinder faces -> empty mesh", strict=True)
def test_native_full_cylinder_not_yet_supported(tmp_path):
    # KNOWN GAP (no geometry left behind): a standalone full cylinder produces 0 triangles natively,
    # though the crane's *partial* cylindrical faces tessellate fine. Tracks the periodic-surface /
    # full-circle-cap fix. xfail(strict) so it flips to a hard failure the moment it's fixed.
    cyl = ada.PrimCyl("cy", (0, 0, 0), (0, 0, 1), 0.4)
    src = tmp_path / "cyl.step"
    (ada.Assembly("m") / (ada.Part("p") / cyl)).to_stp(src)
    out = tmp_path / "cyl.glb"
    stats = native_step_to_glb(src, out, deflection=1.0)
    assert stats["solids"] == 1


def test_native_step_to_glb_unit_cube(tmp_path):
    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    src = tmp_path / "cube.step"
    (ada.Assembly("m") / (ada.Part("p") / box)).to_stp(src)
    out = tmp_path / "cube.glb"
    stats = native_step_to_glb(src, out)
    assert stats["solids"] == 1
    gltf = _glb_json(out)
    # a cube is 12 triangles -> 36 indices, on the single (default-colour) material
    total_idx = sum(gltf["accessors"][p["indices"]]["count"] for m in gltf["meshes"] for p in m["primitives"])
    assert total_idx == 36, f"unit cube should tessellate to 12 triangles (36 indices), got {total_idx}"
