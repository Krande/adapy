import ada
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.writer import part_to_sat_writer


def test_write_basic_plate_sat(example_files, tmp_path):
    reference_file = example_files / "sat_files/flat_plate_sesam_10x10.sat"

    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    a = ada.Assembly() / pl

    sw = part_to_sat_writer(a)
    faces = sw.get_entities_by_type(se.Face)
    assert len(faces) == 1
    sat_str = sw.to_str()

    # Make sure the top-level lines are the same
    for line_a, line_b in zip(sat_str.splitlines(), reference_file.read_text().splitlines()):
        if "gmGeometry" in line_a:
            continue
        if "coedge" in line_a or "edge" in line_a or "vertex" in line_a or "point" in line_a:
            break
        assert line_a == line_b


def test_write_basic_plates_offset_shared_vertex():
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, origin=(10, 10, 0))

    a = ada.Assembly() / (pl, pl2)
    sw = part_to_sat_writer(a)
    faces = sw.get_entities_by_type(se.Face)
    assert len(faces) == 2


def test_write_basic_plates_offset_shared_edge():
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, origin=(10, 5, 0))

    a = ada.Assembly() / (pl, pl2)
    sw = part_to_sat_writer(a)
    faces = sw.get_entities_by_type(se.Face)
    assert len(faces) == 2


def test_write_basic_plates_offset_no_shared():
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1, origin=(0, 0, 2))

    a = ada.Assembly() / (pl, pl2)
    sw = part_to_sat_writer(a)
    faces = sw.get_entities_by_type(se.Face)
    assert len(faces) == 2
