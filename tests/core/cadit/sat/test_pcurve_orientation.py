"""Regression: SAT curved-plate pcurves must not be spuriously reversed.

ACIS authors each coedge's parameter-space (UV) curve already running in the
coedge's direction, so it should be attached to the OCC edge as-is. The old
default reversal heuristic (``ADA_PCURVE_REVERSE=auto``) flipped the UV trim on
many curved plates, collapsing the face to a negative- or zero-area region that
renders as a hole. On the OP1 hull-skin it broke 380 of 4529 curved plates.

``curved_plate.sat`` is a single-face reproduction: its OCC face area comes out
negative under the old reversal and positive (correct) with the no-reverse
default. We assert a positive area to lock the default in.
"""

from __future__ import annotations

import pytest


def _occ_face_areas(advanced_face) -> list[float]:
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods

    import ada
    from ada.geom import Geometry

    occ = ada.Shape("s", Geometry(0, advanced_face)).solid_occ()
    areas = []
    exp = TopExp_Explorer(occ, TopAbs_FACE)
    while exp.More():
        gp = GProp_GProps()
        brepgprop.SurfaceProperties(topods.Face(exp.Current()), gp)
        areas.append(gp.Mass())
        exp.Next()
    return areas


def test_curved_plate_face_has_positive_area(example_files):
    pytest.importorskip("OCC")
    from ada.cadit.sat.store import SatReaderFactory

    rf = SatReaderFactory(str(example_files / "sat_files/curved_plate.sat"))
    rf.load_sat_data_from_file()

    faces = list(rf.iter_advanced_faces())
    assert faces, "expected at least one advanced face in curved_plate.sat"

    for _rec, advanced_face in faces:
        areas = _occ_face_areas(advanced_face)
        assert areas, "advanced face produced no OCC faces"
        # A reversed pcurve trim collapses the area negative or to ~0.
        for area in areas:
            assert area > 1e-3, f"degenerate/reversed face area {area} (pcurve over-reversed?)"
