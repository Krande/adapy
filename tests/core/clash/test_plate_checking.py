import ada
from ada.core.clash_check import find_edge_connected_perpendicular_plates


def test_plate_perpendicular_touching():
    p1x1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1 = ada.Plate("pl1", p1x1, 0.01, orientation=ada.Placement())
    pl1_5 = ada.Plate("pl1_5", p1x1, 0.01, orientation=ada.Placement((0, 0, 0.5)))
    pl2 = ada.Plate("pl2", p1x1, 0.01, orientation=ada.Placement((0, 0, 1)))
    pl3 = ada.Plate("pl3", p1x1, 0.01, orientation=ada.Placement(xdir=(1, 0, 0), zdir=(0, -1, 0)))
    pl4 = ada.Plate("pl4", p1x1, 0.01, orientation=ada.Placement(xdir=(0, 1, 0), zdir=(1, 0, 0)))

    p = ada.Part("MyFem") / [pl1, pl1_5, pl2, pl3, pl4]
    plates = p.get_all_physical_objects(by_type=ada.Plate)

    plate_map = find_edge_connected_perpendicular_plates(plates)

    assert len(plate_map.keys()) == 2 and pl3 in plate_map.keys() and pl4 in plate_map.keys()

    pl3_res = plate_map[pl3]
    pl4_res = plate_map[pl4]

    assert len(pl3_res) == 1
    assert len(pl4_res) == 1
