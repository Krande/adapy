import ada
from ada.base.types import GeomRepr
from ada.cadit.sat.write.write_plate import plate_to_sat_entities
from ada.cadit.sat.write.writer import part_to_sat_writer


def test_write_basic_plate_sat(example_files, tmp_path):
    reference_file = example_files / "sat_files/flat_plate_sesam_10x10.sat"

    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)

    sat_str = """2000 0 1 0
18 SESAM - gmGeometry 14 ACIS 30.0.1 NT 24 Tue Jan 17 20:39:08 2023
1000 9.9999999999999995e-07 1e-10
"""
    sat_entities = plate_to_sat_entities(pl, GeomRepr.SHELL)
    sat_str += "\n".join([s.to_string() for s in sat_entities])
    sat_str += "\nEnd-of-ACIS-data"

    # Make sure the top-level lines are the same
    for line_a, line_b in zip(sat_str.splitlines(), reference_file.read_text().splitlines()):
        if 'coedge' in line_a or 'edge' in line_a or 'vertex' in line_a or 'point' in line_a:
            break
        assert line_a == line_b


def test_write_basic_plates_offset_shared_vertex():
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, origin=(10, 10, 0))

    a = ada.Assembly() / (pl, pl2)
    sw = part_to_sat_writer(a)

def test_write_basic_plates_offset_shared_edge():
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, origin=(10, 5, 0))

    a = ada.Assembly() / (pl, pl2)
    sat_str = part_to_sat_writer(a)

def test_write_basic_plates_offset_no_shared():
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1, origin=(0, 0, 2))

    a = ada.Assembly() / (pl, pl2)
    sat_str = part_to_sat_writer(a)