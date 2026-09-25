"""Regression: SAT curved-plate pcurves must not be spuriously reversed.

ACIS authors each coedge's parameter-space (UV) curve already running in the
coedge's direction, so it should be attached to the OCC edge as-is. The old
default reversal heuristic (``ADA_PCURVE_REVERSE=auto``) flipped the UV trim on
many curved plates, collapsing the face to a negative- or zero-area region that
renders as a hole. On the OP1 hull-skin it broke 380 of 4529 curved plates.

``curved_plate.sat`` is a single-face reproduction: its face area comes out
negative under the old reversal and positive (correct) with the no-reverse
default. We assert a positive area on each installed kernel to lock the default in.
"""

from __future__ import annotations


def _face_areas(backend, advanced_face) -> list[float]:
    """Signed area of each face the advanced face builds to, on ``backend`` (GProp mass: a
    reversed trim comes out negative)."""
    import ada
    from ada.geom import Geometry

    shape = backend.build(ada.Shape("s", Geometry(0, advanced_face)).solid_geom())
    return [backend.area(f) for f in backend.faces(shape)]


def test_curved_plate_face_has_positive_area(example_files, backend):
    from ada.cadit.sat.store import SatReaderFactory

    rf = SatReaderFactory(str(example_files / "sat_files/curved_plate.sat"))
    rf.load_sat_data_from_file()

    faces = list(rf.iter_advanced_faces())
    assert faces, "expected at least one advanced face in curved_plate.sat"

    for _rec, advanced_face in faces:
        areas = _face_areas(backend, advanced_face)
        assert areas, "advanced face produced no faces"
        # A reversed pcurve trim collapses the area negative or to ~0.
        for area in areas:
            assert area > 1e-3, f"degenerate/reversed face area {area} (pcurve over-reversed?)"
