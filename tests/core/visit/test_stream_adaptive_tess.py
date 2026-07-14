"""The streaming STEP->GLB libtess2 path honours adaptive tessellation coarsening
(ADA_STREAM_TESS_MODEL_SCALE) — small features in a large model tessellate coarser, so a big
assembly (e.g. the boiler) doesn't over-tessellate every small pipe/torus into a slivery crows-nest.

Regression for scene_from_step_stream, which previously tessellated at a fixed angular ceiling with
no model_scale (unlike native_step_to_glb, which already coarsens by default).
"""

from __future__ import annotations

import json
import struct

import pytest


def _glb_tris(path: str) -> int:
    d = open(path, "rb").read()
    jlen = struct.unpack("<I", d[12:16])[0]
    g = json.loads(d[20 : 20 + jlen])
    return sum(g["accessors"][p["indices"]]["count"] // 3 for m in g["meshes"] for p in m["primitives"])


def test_stream_model_scale_coarsens(tmp_path, monkeypatch):
    pytest.importorskip("adacpp")
    import ada
    from ada.cad import active_backend

    if not hasattr(active_backend(), "tessellate_stream"):
        pytest.skip("adacpp build has no tessellate_stream")
    from ada.visit.scene_handling.scene_from_step_stream import (
        StepStreamSource,
        convert_step_stream_to_glb,
    )

    # small cylinders (r=15) spread across a large extent -> features << model scale
    objs = [ada.PrimCyl(f"c{i}", (i * 2000, 0, 0), (i * 2000, 0, 200), 15) for i in range(5)]
    step = tmp_path / "cyls.stp"
    (ada.Assembly("t") / (ada.Part("p") / objs)).to_stp(str(step))

    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")

    monkeypatch.setenv("ADA_STREAM_TESS_MODEL_SCALE", "0")  # adaptive off
    convert_step_stream_to_glb(StepStreamSource(str(step)), str(tmp_path / "off.glb"))
    fine = _glb_tris(str(tmp_path / "off.glb"))

    monkeypatch.setenv("ADA_STREAM_TESS_MODEL_SCALE", "20000")  # features << 1% => coarsened
    convert_step_stream_to_glb(StepStreamSource(str(step)), str(tmp_path / "on.glb"))
    coarse = _glb_tris(str(tmp_path / "on.glb"))

    assert fine > 0 and coarse > 0
    assert coarse < fine * 0.5, f"adaptive coarsening had no effect: {coarse} vs {fine}"
