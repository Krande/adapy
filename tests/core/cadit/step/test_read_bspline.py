from OCC.Core.TopoDS import TopoDS_Shell

from ada.cadit.step.read.geom.surfaces import occ_shell_to_ada_faces
from ada.occ.utils import extract_occ_shapes


def test_read_b_spline_surf_w_knots_2_step(example_files, tmp_path, monkeypatch):
    step_path = example_files / "sat_files/hullskin_face_0.stp"
    shapes = extract_occ_shapes(step_path, scale=1.0, transform=None, rotate=None, include_shells=True)

    assert len(shapes) == 1
    shp = shapes[0]

    # Test if shape is a shell
    assert isinstance(shp, TopoDS_Shell)
    # Convert shell to list of AdvancedFaces
    ada_faces = occ_shell_to_ada_faces(shp)
    assert len(ada_faces) > 0, "Shell should contain at least one face"

    # Validate each AdvancedFace
    for ada_face in ada_faces:
        assert ada_face is not None, "AdvancedFace should not be None"
        assert ada_face.face_surface is not None, "AdvancedFace should have a face_surface"
        assert len(ada_face.bounds) > 0, "AdvancedFace should have at least one boundary"
