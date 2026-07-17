"""make_face_from_geom must return BRepCheck-valid OCC faces.

An AdvancedFace read from an ACIS SAT can leave an edge without a usable 2D
curve-on-surface: a plane face bounded by arcs carries no pcurve (implicit for
planes, so none is stored), and a B-spline face's SAT pcurves are not always
attached cleanly by the OCC-edge builder. The resulting OCC face renders fine
but is BRepCheck-invalid, which hard-crashes any boolean operation on it —
notably the beam-imprint General Fuse used when writing a Genie SAT (it
segfaults on an invalid face). ``make_face_from_geom`` heals such faces at
source, so this asserts its output is valid.

The fixtures are the exact AdvancedFaces (pickled) that came out invalid before
the heal — one plane face bounded by arcs, two rational B-spline faces whose
SAT pcurves the builder dropped. Without the heal these each assert False.
"""

import pickle
from pathlib import Path

import pytest

pytest.importorskip("OCC")

FIXTURES = Path(__file__).parent / "bad_face_fixtures"


@pytest.mark.parametrize(
    "fixture",
    ["planar_arc_face.pkl", "rational_bspline_face_0.pkl", "rational_bspline_face_1.pkl"],
)
def test_make_face_from_geom_is_brepcheck_valid(fixture):
    from OCC.Core.BRepCheck import BRepCheck_Analyzer

    from ada.occ.geom.surfaces import make_face_from_geom

    advanced_face = pickle.loads((FIXTURES / fixture).read_bytes())
    face = make_face_from_geom(advanced_face)
    assert not face.IsNull()
    assert BRepCheck_Analyzer(face).IsValid(), f"{fixture} produced a BRepCheck-invalid OCC face"
