"""Disk-spilled streaming STEP -> GLB (ada.cadit.step.glb_spill).

The streaming GLB path spills each solid's per-material mesh to a temp file and assembles
the GLB by streaming those files into the BIN chunk — never holding the merged model or
the GLB bytes. These tests pin the picking contract (scenes[0].extras: id_hierarchy +
in-bounds draw_ranges), temp-dir hygiene (success AND failure), and that the output is an
equivalent substitute for the old trimesh scene.export (same materials + draw ranges).
"""

import glob
import json
import os
import struct
import tempfile

import pytest

import ada
from ada.cadit.step.stream_to_glb import stream_step_to_glb
from ada.visit.colors import Color


def _tree(glb_bytes: bytes) -> dict:
    jlen = struct.unpack("<I", glb_bytes[12:16])[0]
    return json.loads(glb_bytes[20 : 20 + jlen])


def _colored_step(tmp_path):
    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    box.color = Color(1.0, 0.0, 0.0)
    cyl = ada.PrimCyl("cy", (2, 0, 0), (2, 0, 1), 0.4)
    cyl.color = Color(0.0, 0.0, 1.0)
    src = tmp_path / "c.step"
    (ada.Assembly("m") / (ada.Part("p") / [box, cyl])).to_stp(src)
    return src


def _spill_dirs() -> set[str]:
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "ada_glb_spill_*")))


def test_spill_glb_picking_metadata_and_in_bounds(tmp_path):
    glb = tmp_path / "c.glb"
    stats = stream_step_to_glb(_colored_step(tmp_path), glb)
    assert stats["meshed"] == 2

    import trimesh

    scene = trimesh.load(glb)  # spec-valid GLB round-trips
    assert sum(len(g.faces) for g in scene.geometry.values()) > 0

    t = _tree(glb.read_bytes())
    extras = t["scenes"][0]["extras"]
    assert "id_hierarchy" in extras
    draw_keys = [k for k in extras if k.startswith("draw_ranges_node")]
    assert len(draw_keys) == 2  # one per merged material

    # every draw range is within its mesh's index accessor, and POSITION carries min/max
    for mesh in t["meshes"]:
        prim = mesh["primitives"][0]
        count = t["accessors"][prim["indices"]]["count"]
        pos_acc = t["accessors"][prim["attributes"]["POSITION"]]
        assert len(pos_acc["min"]) == 3 and len(pos_acc["max"]) == 3
        for _node_id, (start, length) in extras["draw_ranges_" + mesh["name"]].items():
            assert 0 <= start <= start + length <= count


def test_spill_tmpdir_cleaned_on_success(tmp_path):
    before = _spill_dirs()
    stream_step_to_glb(_colored_step(tmp_path), tmp_path / "c.glb")
    assert _spill_dirs() <= before  # no spill dir left behind


def test_spill_tmpdir_cleaned_on_exception(tmp_path, monkeypatch):
    import ada.cadit.step.glb_spill as glb_spill

    def boom(*_a, **_k):
        raise RuntimeError("boom during assembly")

    monkeypatch.setattr(glb_spill, "write_glb_from_spill", boom)
    before = _spill_dirs()
    with pytest.raises(RuntimeError):
        stream_step_to_glb(_colored_step(tmp_path), tmp_path / "c.glb")
    assert _spill_dirs() <= before  # try/finally cleanup ran despite the failure


def test_spill_glb_equivalent_to_trimesh_export(tmp_path):
    # The disk-spilled assembler is a faithful substitute for the old in-memory
    # scene.export: same merged materials/colours and the same per-solid picking ranges.
    from ada.visit.scene_converter import SceneConverter
    from ada.visit.scene_handling.scene_from_step_stream import StepStreamSource

    src = _colored_step(tmp_path)
    glb = tmp_path / "new.glb"
    stream_step_to_glb(src, glb)
    new = _tree(glb.read_bytes())
    old = _tree(SceneConverter(source=StepStreamSource(src)).build_glb())

    assert len(new["materials"]) == len(old["materials"])
    new_colors = sorted(tuple(m["pbrMetallicRoughness"]["baseColorFactor"]) for m in new["materials"])
    old_colors = sorted(tuple(m["pbrMetallicRoughness"]["baseColorFactor"]) for m in old["materials"])
    assert new_colors == old_colors

    def _draw(tree):
        return {k: v for k, v in tree["scenes"][0]["extras"].items() if k.startswith("draw_ranges_node")}

    assert _draw(new) == _draw(old)  # identical per-solid picking contract
