"""The libtess2 streamed-GLB path must still emit the full adapy viewer contract.

ADA_STREAM_TESS_PIPELINE=libtess2 routes _tessellate_geom_worker through adacpp's NGEOM
tessellation instead of OCC build+tessellate. The rest of convert_step_stream_to_glb is
unchanged, so the output must keep: the ADA_EXT_data extension (lineage), scene id_hierarchy,
per-material picking draw-ranges, and real mesh-referencing nodes — built fresh (no step2glb
metadata). A single box stays under the sequential threshold, so no process pool is spawned.
"""

from __future__ import annotations

import pytest


def test_libtess2_stream_glb_keeps_adapy_contract(tmp_path, monkeypatch):
    ada = pytest.importorskip("ada")
    pytest.importorskip("adacpp")
    pygltflib = pytest.importorskip("pygltflib")

    monkeypatch.setenv("ADAPY_CAD_BACKEND", "adacpp")
    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")

    src = tmp_path / "box.step"
    (ada.Assembly("a") / (ada.Part("p") / ada.PrimBox("b", (0, 0, 0), (1, 1, 1)))).to_stp(src)
    out = tmp_path / "box.glb"

    from ada.cadit.step.stream_to_glb import stream_step_to_glb

    stats = stream_step_to_glb(src, out, tolerant=True)
    assert stats["meshed"] >= 1 and stats["skipped"] == 0

    g = pygltflib.GLTF2().load(str(out))
    # contract: ADA_EXT_data extension present
    assert g.extensions and "ADA_EXT_data" in g.extensions
    assert "ADA_EXT_data" in (g.extensionsUsed or [])
    # contract: at least one node references a mesh (not orphaned geometry)
    assert any(n.mesh is not None for n in g.nodes)
    # contract: scene extras carry the id hierarchy + per-material picking draw-ranges
    extras = g.scenes[g.scene or 0].extras or {}
    assert "id_hierarchy" in extras
    assert any(k.startswith("draw_ranges_node") for k in extras)
