import numpy as np

import ada
from ada.config import Settings
from ada.core.vector_utils import vector_length

test_dir = Settings.test_dir / "plates"


def test_2dinit(place1):
    from common import dummy_display

    pl1 = ada.Plate("MyPl", [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)], 20e-3, **place1)
    dummy_display(pl1)


def test_roundtrip_fillets(place1, place2):
    a = ada.Assembly("ExportedPlates")
    p = ada.Part("MyPart")
    a.add_part(p)
    pl1 = ada.Plate("MyPl", [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)], 20e-3, **place1)
    p.add_plate(pl1)

    pl2 = ada.Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **place2)
    p.add_plate(pl2)

    a.to_ifc(test_dir / "my_plate_simple.ifc")

    b = ada.Assembly("MyReimport")
    b.read_ifc(test_dir / "my_plate_simple.ifc")
    b.to_ifc(test_dir / "my_plate_simple_re_exported.ifc")


def test_2ifc_simple(place2):
    pl2 = ada.Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **place2)
    seg_list = pl2.poly.seg_list
    seg1 = seg_list[0]
    assert len(pl2.poly.seg_list) == 6
    assert type(seg1) is ada.ArcSegment

    # (ada.Assembly("ExportedPlates") / (ada.Part("MyPart") / pl2)).to_ifc(test_dir / "my_plate_poly.ifc")


def test_triangle():
    pl = ada.Plate("test", [(0, 0), (1, 0, 0.1), (0.5, 0.5)], 20e-3)
    assert len(pl.poly.seg_list) == 4

    # (ada.Assembly() / [ada.Part("te") / pl]).to_ifc(test_dir / "triangle_plate.ifc")


def test_floaty_input_ex1():
    origin = [362237.0037951513, 100000.0, 560985.9095763591]
    csys = [
        [0.0, -1.0, 0.0],
        [-0.4626617625735456, 0.0, 0.8865348799975894],
        [-0.8865348799975894, 0.0, -0.4626617625735456],
    ]
    local_points2d = [
        [4.363213751783499e-11, 229.80445306040926],
        [1.4557793281525672e-11, -57.217605078163196],
        [2.912511939794014e-11, -207.22885580839431],
        [-330.0, -207.25, -4.518217213237464e-11],
        [-400.0, -122.2, 50.0],
        [-400.0, 42.79, 50.0],
        [-325.0, 267.83, 50.0],
        [-85.00004587126028, 650.0198678083951, 24.999999999931404],
        [-35.0, 650.02, -7.881391015070461e-12],
        [-35.0, 350.1],
        [-15.14, 261.52],
    ]
    thick = 30
    pl = ada.Plate(
        "test", local_points2d, thick, placement=ada.Placement(origin=origin, zdir=csys[2], xdir=csys[0]), units="mm"
    )
    assert tuple(pl.placement.origin) == tuple(origin)
    assert tuple(pl.placement.zdir) == tuple(csys[2])
    assert vector_length(pl.nodes[0].p - np.array([362130.68206185, 100000.0, 561189.63923958])) < 1e-8

    # (ada.Assembly() / [ada.Part("te") / pl]).to_ifc(test_dir / "error_plate.ifc")


def test_ex2():
    origin = [362857.44778571784, 100000.0, 561902.5557556185]
    csys = [
        [0.0, 1.0, 0.0],
        [-0.9756456931466083, 0.0, 0.2193524138104576],
        [0.2193524138104576, 0.0, 0.9756456931466083],
    ]
    local_points2d = [
        [-35.000000000029075, 246.8832348783828],
        [-15.0, 154.3],
        [6.4472604556706474e-15, 74.98044924694155],
        [0.0, -170.7],
        [-3.376144703353855e-14, -320.6997855533479],
        [-330.0, -320.7, -1.0189517968329821e-10],
        [-400.0, -235.7, 50.0],
        [-400.0, -70.7, 50.0],
        [-325.0, 154.3, 50.0],
        [-85.00004587117287, 500.03303441608927, 24.99999999997542],
        [-34.99999999999994, 500.0330344161669, -7.881391015070461e-12],
    ]
    thick = 30
    pl = ada.Plate(
        "test2", local_points2d, thick, placement=ada.Placement(origin=origin, zdir=csys[2], xdir=csys[0]), units="mm"
    )
    assert tuple(pl.placement.origin) == tuple(origin)
    # a = (ada.Assembly(units="mm") / [ada.Part("te", units="mm") / pl]).to_ifc(test_dir / "error_plate2.ifc")
