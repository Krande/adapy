"""Regression: Beam → STEP → from_step → to_gltf round-trip.

Reported by a downstream consumer: ``Assembly.to_gltf()`` raised
``AttributeError: 'TopoDS_Solid' object has no attribute 'geometry'``
on any assembly built from ``ada.from_step`` of a STEP file produced
by ``ada.Beam(...).to_stp()``.

Root cause: ``read_step_file`` stuffs the raw OCC ``TopoDS_Shape`` it
gets back from ``extract_occ_shapes`` straight onto ``Shape._geom``
without an ``ada.geom.Geometry`` wrapper. The tessellation pipeline
then calls ``Shape.solid_geom()`` which dereferenced ``self.geom.geometry``
on a TopoDS_Solid and exploded.

The fix routes raw-OCC shapes directly to ``tessellate_occ_geom``,
skipping the Geometry → OCC step that has nothing to convert from.
"""
from __future__ import annotations

import ada


def test_beam_step_roundtrip_to_gltf(tmp_path):
    bm = ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300")
    a1 = ada.Assembly() / bm

    stp_path = tmp_path / "cad.stp"
    a1.to_stp(stp_path)
    assert stp_path.exists()

    a2 = ada.from_step(stp_path)
    glb_path = tmp_path / "cad.glb"
    a2.to_gltf(glb_path)
    # Non-zero file = tessellation succeeded and produced binary geometry.
    assert glb_path.exists()
    assert glb_path.stat().st_size > 0
