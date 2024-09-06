from ada.cadit.sat.store import SatReaderFactory


def test_read_b_spline_surf_w_knots(example_files, tmp_path):
    sat_reader = SatReaderFactory(example_files / "sat_files/curved_plate.sat")
    advanced_faces = list(sat_reader.iter_advanced_faces())
    assert len(advanced_faces) == 1
