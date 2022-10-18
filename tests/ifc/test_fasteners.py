import ada
from ada.concepts.fasteners import WeldVProfile


def test_butt_weld():
    pl_points = [(0, 0), (1, 0.0), (1, 1), (0, 1)]

    pl1 = ada.Plate("pl1", pl_points, 0.01)
    pl2 = ada.Plate("pl2", pl_points, 0.01, placement=ada.Placement((1.0, 0, 0)))
    o = (1, 0, 0)
    wp = WeldVProfile("VProfile", [(-0.005, 0.01), (0, 0), (0.005, 0.01)], o, (0, 1, 0), (-1, 0, 0))
    wld = ada.Weld("pl_fastener", o, (1, 1, 0), [pl1, pl2], wp)
    a = ada.Assembly() / (pl1, pl2, wld)
    a.to_ifc("temp/welded_plates.ifc", validate=True, file_obj_only=True)
