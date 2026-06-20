"""step2glb STEP -> GLB pipeline (ada.cadit.step.step2glb_to_glb).

The pipeline runs step2glb in ``--merged`` mode so the GLB already carries
adapy's viewer picking/tree contract (one node/mesh per colour, per-part
``draw_ranges_node<matid>`` + ``id_hierarchy`` in ``scenes[0].extras``) — no
metadata post-pass, nothing for the frontend to special-case. ``--up-axis y``
keeps the model in adapy's native Z-up frame.

Gated on the step2glb binary being present (``ADAPY_STEP2GLB_BIN`` / bundled /
PATH). All fixtures are synthetic — no client data.
"""

import json
import struct

import pytest

import ada
from ada.cadit.step.step2glb_to_glb import convert_step_to_glb, is_available

pytestmark = pytest.mark.skipif(not is_available(), reason="step2glb binary not available")


def _glb_tree(glb_path) -> dict:
    raw = glb_path.read_bytes()
    jlen = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20 : 20 + jlen])


def test_step2glb_merged_renders_and_carries_viewer_contract(tmp_path):
    # Synthetic model with a curved solid (cylinder) — the surface family the
    # streaming reader drops but step2glb renders. Two colours -> two materials.
    from ada.visit.colors import Color

    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    box.color = Color(1, 0, 0)
    cyl = ada.PrimCyl("cy", (2, 0, 0), (2, 0, 1), 0.4)
    cyl.color = Color(0, 0, 1)
    src = tmp_path / "synthetic.step"
    (ada.Assembly("m") / (ada.Part("p") / [box, cyl])).to_stp(src)

    glb = tmp_path / "synthetic.glb"
    convert_step_to_glb(src, glb)
    assert glb.exists() and glb.stat().st_size > 0

    # renders: trimesh loads triangles
    import trimesh

    scene = trimesh.load(glb)
    assert sum(len(g.faces) for g in scene.geometry.values()) > 0

    tree = _glb_tree(glb)
    # merged-by-colour: one node/mesh per colour, named node<matid>
    assert all(n["name"].startswith("node") for n in tree["nodes"] if "mesh" in n)
    assert len(tree["meshes"]) == len(tree["materials"])  # one mesh per colour

    # the viewer contract lives in scene extras
    extras = tree["scenes"][0]["extras"]
    assert "id_hierarchy" in extras
    assert sum(1 for v in extras["id_hierarchy"].values() if v[1] == "*") == 1  # single root
    draw_keys = [k for k in extras if k.startswith("draw_ranges_node")]
    assert draw_keys, "no draw_ranges_node* in scene extras"
    # every draw range id resolves to a hierarchy name; ranges are index units
    for key in draw_keys:
        for nid, (start, length) in extras[key].items():
            assert length > 0
            assert nid in extras["id_hierarchy"]

    # no leftover hierarchical bloat: node count is on the order of colours, not parts
    assert len(tree["nodes"]) <= len(tree["materials"]) + 1


def test_step2glb_up_axis_matches_adapy_z_up(tmp_path):
    # adapy keeps native Z-up and does not rotate on GLB export; the pipeline must
    # match. A 6 m-tall beam must span ~6 on Z (not Y, as step2glb's default
    # up-axis=z rotation would produce).
    import trimesh

    bm = ada.Beam("bm", (0, 0, 0), (0, 0, 6), ada.Section("s", from_str="IPE300"))
    src = tmp_path / "tall.step"
    (ada.Assembly("m") / (ada.Part("p") / bm)).to_stp(src)

    glb = tmp_path / "tall.glb"
    convert_step_to_glb(src, glb)

    scene = trimesh.load(glb)
    dx, dy, dz = (scene.bounds[1] - scene.bounds[0])
    assert dz > 5.5, f"height landed off Z (span x={dx:.2f} y={dy:.2f} z={dz:.2f}) — up-axis wrong"
    assert dz > dy and dz > dx
