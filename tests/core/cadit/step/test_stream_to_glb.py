"""Memory-bounded streaming STEP -> GLB (ada.cadit.step.stream_to_glb).

Streams the reader one solid at a time, tessellates via the active CAD backend, and
appends each mesh to the GLB — never holding the whole model. Runs under OCC and
adacpp (backend-neutral mesh contract).
"""

import ada
from ada import Beam, Plate, Section
from ada.cadit.step.stream_to_glb import stream_step_to_glb


def test_stream_step_to_glb_round_trip(tmp_path):
    a = ada.Assembly("m") / (
        ada.Part("p")
        / [
            Beam("ipe", (0, 0, 0), (3, 0, 0), Section("ipe", from_str="IPE300")),
            Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02),
        ]
    )
    src = tmp_path / "m.step"
    a.to_stp(src)  # OCC writer (forward references -> two-pass reader path)

    glb = tmp_path / "m.glb"
    stats = stream_step_to_glb(src, glb, tolerant=True)

    assert stats["meshed"] >= 2
    assert glb.exists() and glb.stat().st_size > 0

    # the GLB loads back as a scene with the meshed geometry
    import trimesh

    scene = trimesh.load(glb)
    assert sum(len(g.faces) for g in scene.geometry.values()) > 0
