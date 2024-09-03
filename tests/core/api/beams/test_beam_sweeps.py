from ada import Assembly, BeamSweep, CurvePoly2d, Part


def test_sweep_beam(tmp_path):
    curve = CurvePoly2d.from_3d_points([(10, 0, 0), (11, 5.0, 0.0, 3), (10, 10, 0)])
    bm = BeamSweep("MyBeam", sec="IPE600", curve=curve)
    a = Assembly("ExportedPlates", units="m") / (Part("MyPart") / bm)
    _ = a.to_ifc(tmp_path / "my_swept_beam_elem_m.ifc", file_obj_only=False, validate=True)
