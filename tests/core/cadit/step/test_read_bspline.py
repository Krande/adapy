from ada.cad import active_backend
from ada.cad.doc import active_doc_backend


def test_read_b_spline_surf_w_knots_2_step(example_files):
    step_path = example_files / "sat_files/hullskin_face_0.stp"

    backend = active_backend()
    root = active_doc_backend().step_reader(step_path).get_root_shape()

    shells = backend.shells(root)
    assert len(shells) >= 1, "STEP body should contain at least one shell"
    assert backend.shape_type(shells[0]) == "shell"

    faces = backend.faces(shells[0])
    assert len(faces) > 0, "Shell should contain at least one face"

    # Decompose each B-spline face into an AdvancedFace via the active backend.
    n_bspline = 0
    for face in faces:
        if backend.face_surface_type(face) != "bspline":
            continue
        ada_face = backend.face_to_advanced_face(face)
        assert ada_face is not None, "AdvancedFace should not be None"
        assert ada_face.face_surface is not None, "AdvancedFace should have a face_surface"
        assert len(ada_face.bounds) > 0, "AdvancedFace should have at least one boundary"
        n_bspline += 1
    assert n_bspline > 0, "Shell should contain at least one B-spline face"
