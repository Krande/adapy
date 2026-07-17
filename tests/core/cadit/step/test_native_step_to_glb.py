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
from ada.cadit.step.native_step_to_glb import (
    native_adacpp_available,
    native_step_to_glb,
    native_track_selection_available,
)

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

    # picking contract: scenes[0].extras carries id_hierarchy + a draw_ranges_node* per material,
    # and the ADA_EXT_data extension marks it a design model.
    extras = gltf["scenes"][0]["extras"]
    assert "id_hierarchy" in extras and len(extras["id_hierarchy"]) >= 2, "per-solid id_hierarchy"
    dr_keys = [k for k in extras if k.startswith("draw_ranges_node")]
    assert len(dr_keys) == 2, "one draw_ranges_node* per material"
    # each draw range is [start, length] into that material's index buffer
    for k in dr_keys:
        for _nid, rng in extras[k].items():
            assert len(rng) == 2 and rng[1] > 0
    # ADA_EXT_data must carry the fields the viewer reads (it accesses design_objects.length etc.) —
    # a partial object crashes the viewer ("cannot read properties of undefined (reading 'length')").
    ada_ext = gltf.get("extensions", {}).get("ADA_EXT_data")
    assert ada_ext is not None, "ADA_EXT_data extension present"
    assert isinstance(ada_ext.get("design_objects"), list), "design_objects is a list"
    assert isinstance(ada_ext.get("simulation_objects"), list), "simulation_objects is a list"
    assert "version" in ada_ext and "assembly_guid" in ada_ext, "version + assembly_guid present"


def test_native_full_cylinder_renders(tmp_path):
    # No geometry left behind: a standalone full (360deg) cylinder must tessellate. Its edges are
    # SURFACE_CURVE / SEAM_CURVE wrappers (OCC export) around the CIRCLE / LINE 3D curves — the native
    # reader unwraps them, so the side + both circular caps mesh (regression guard for that fix).
    cyl = ada.PrimCyl("cy", (0, 0, 0), (0, 0, 1), 0.4)
    src = tmp_path / "cyl.step"
    (ada.Assembly("m") / (ada.Part("p") / cyl)).to_stp(src)
    out = tmp_path / "cyl.glb"
    stats = native_step_to_glb(src, out, deflection=1.0)
    assert stats["solids"] == 1
    gltf = _glb_json(out)
    total_idx = sum(gltf["accessors"][p["indices"]]["count"] for m in gltf["meshes"] for p in m["primitives"])
    assert total_idx > 0, "full cylinder must produce triangles (side + caps)"


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


def _curved_step(tmp_path):
    """A curved solid: the tracks differ on curved trims, not on a box's planar faces."""
    c = ada.PrimCyl("c", (0, 0, 0), (0, 0, 2), 1.0)
    src = tmp_path / "curved.step"
    (ada.Assembly("m") / (ada.Part("p") / [c])).to_stp(src)
    return src


@pytest.mark.skipif(
    not native_track_selection_available(), reason="adacpp build predates native track selection (pipeline kwarg)"
)
def test_native_track_selection_reaches_the_kernel(tmp_path):
    """A selected track must CHANGE the mesh.

    The C++ core always accepted `pipeline`, but the python binding never forwarded it, so every
    native conversion ran libtess2 whatever the caller chose — accepted and ignored, the failure
    mode that reads as success. Comparing two tracks' output is the only assertion that catches a
    regression back to that: asserting the call merely succeeds would pass against it.
    """
    src = _curved_step(tmp_path)
    counts = {}
    for track in ("libtess2", "cdt"):
        out = tmp_path / f"{track}.glb"
        native_step_to_glb(src, out, deflection=0.05, angular_deg=10.0, meshopt=False, pipeline=track)
        gltf = _glb_json(out)
        counts[track] = sum(gltf["accessors"][p["indices"]]["count"] for m in gltf["meshes"] for p in m["primitives"])

    assert all(v > 0 for v in counts.values()), f"both tracks must mesh the solid: {counts}"
    assert counts["libtess2"] != counts["cdt"], f"track ignored — both produced {counts['libtess2']} indices"


@pytest.mark.skipif(
    not native_track_selection_available(), reason="adacpp build predates native track selection (pipeline kwarg)"
)
def test_native_refuses_a_taxonomy_track(tmp_path):
    """The taxonomy kernels need ifcopenshell geometry the C++ STEP reader never builds. adacpp does
    not error on them here — it meshes as though untracked — so adapy must refuse rather than return
    a GLB attributed to a kernel that never ran."""
    src = _curved_step(tmp_path)
    with pytest.raises(RuntimeError, match="taxonomy track"):
        native_step_to_glb(src, tmp_path / "occ.glb", deflection=0.05, pipeline="occ")
