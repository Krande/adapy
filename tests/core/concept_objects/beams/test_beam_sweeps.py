from ada import Assembly, BeamSweep, CurvePoly, Part
from ada.config import Settings

test_dir = Settings.test_dir / "beams"


def test_sweep_beam():
    curve = CurvePoly(points3d=[(10, 0, 0), (11, 5.0, 0.0, 3), (10, 10, 0)])
    bm = BeamSweep("MyBeam", sec="IPE600", curve=curve)
    a = Assembly("ExportedPlates", units="m") / (Part("MyPart") / bm)
    _ = a.to_ifc(test_dir / "my_swept_beam_elem_m.ifc", file_obj_only=False, validate=True)
