import ada
from ada.base.types import GeomRepr
from ada.cadit.sat.write.write_plate import plate_to_sat_body


def test_write_basic_plate_sat(example_files, tmp_path):
    reference_file = example_files / "/sat_files/flat_plate_sesam_10x10.sat"
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)

    sat_str = """2000 0 1 0
18 SESAM - gmGeometry 14 ACIS 30.0.1 NT 24 Tue Jan 17 20:39:08 2023
1000 9.9999999999999995e-07 1e-10"""
    sat_str += plate_to_sat_body(pl, 0, geo_repr=GeomRepr.SHELL)
    sat_str += "End-of-ACIS-data"

    assert sat_str == reference_file.read_text()
