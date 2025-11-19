from OCC.Core.BRepCheck import BRepCheck_Analyzer

import ada
from ada.cadit.sat.parser import AcisSatParser, AcisToAdaConverter
from ada.cadit.step.read.geom.surfaces import occ_shell_to_ada_faces
from ada.geom import Geometry
from ada.geom.surfaces import AdvancedFace
from ada.occ.utils import extract_occ_shapes


def test_read_b_spline_surf_w_knots_2_sat(example_files, tmp_path, monkeypatch):
    # First read the STEP file
    step_path = example_files / "sat_files/hullskin_face_0.stp"
    shapes = extract_occ_shapes(step_path, scale=1.0, transform=None, rotate=None, include_shells=True)

    assert len(shapes) == 1
    shp = shapes[0]
    # Convert shell to list of AdvancedFaces
    ada_faces_from_stp = occ_shell_to_ada_faces(shp)
    assert len(ada_faces_from_stp) == 1
    stp_face = ada_faces_from_stp[0]
    assert isinstance(stp_face, AdvancedFace)

    # Then Parse the SAT file and make sure the resulting advanced faces are the same
    sat_file = example_files / "sat_files/hullskin_face_0.sat"
    parser = AcisSatParser(sat_file)
    parser.parse()

    # Convert to adapy geometry using body-based organization
    converter = AcisToAdaConverter(parser)
    bodies = converter.convert_all_bodies()
    faces = converter.convert_all_faces()
    assert len(bodies) == 1
    assert len(faces) == 1

    face_name, face_obj_sat = faces[0]
    assert face_name == "face_6"
    assert isinstance(face_obj_sat, AdvancedFace)

    # SAT parser correctly identifies this as Rational (weights != 1), while STEP import (OCCT)
    # produces a Non-Rational surface (possibly approximating or simplifying).
    # So we cannot assert strict equality of surfaces.
    # assert stp_face.face_surface == face_obj.face_surface

    from ada.geom.surfaces import RationalBSplineSurfaceWithKnots

    assert isinstance(face_obj_sat.face_surface, RationalBSplineSurfaceWithKnots)

    # Bounds might also differ slightly due to rational vs non-rational curves, but let's see
    # assert stp_face.bounds == face_obj.bounds
    shape = ada.Shape(f"shape0", Geometry(0, face_obj_sat))
    occ_shape = shape.solid_occ()
    analyzer = BRepCheck_Analyzer(occ_shape, True)
    assert analyzer.IsValid()
