import logging

import numpy as np
import pytest
from ifcopenshell.validate import validate

import ada
from ada import Placement
from ada.core.utils import set_list_first_position_elem
from ada.geom import solids as geo_so
from ada.occ.utils import get_points_from_occ_shape, iter_faces_with_normal


@pytest.fixture
def place1() -> Placement:
    return Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, 0, 1))


@pytest.fixture
def place2() -> Placement:
    return Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, -1, 0))


@pytest.fixture
def place3() -> Placement:
    return Placement(origin=(0, 0, 0), xdir=(0, 1, 0), zdir=(1, 0, 0))


def test_flat_xy_plate_shell(place1):
    pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place1)

    occ_verts_sh2 = get_points_from_occ_shape(pl2.shell_occ())
    origin_index = occ_verts_sh2.index(tuple(place1.origin))

    # shift the list so that the origin is the first point
    occ_verts_sh2 = occ_verts_sh2[origin_index:] + occ_verts_sh2[:origin_index]

    # Feed the points back to the Plate constructor and assert that the plate is the same
    p2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=place1.xdir)

    # Check that the plate is placed correctly
    place_r = p2_r.poly.orientation
    assert place_r.xdir.is_equal(pl2.poly.xdir)
    assert place_r.ydir.is_equal(pl2.poly.ydir)
    assert place_r.zdir.is_equal(pl2.poly.normal)
    assert place_r.origin.is_equal(pl2.poly.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, p2_r.poly.points2d):
        assert p1.is_equal(p2)


def test_flat_xz_plate_shell(place2):
    pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place2)
    place = pl2.poly

    occ_verts_sh2 = get_points_from_occ_shape(pl2.shell_occ())
    occ_verts_sh2 = set_list_first_position_elem(occ_verts_sh2, tuple(pl2.poly.origin))

    # Feed the points back to the Plate constructor and assert that the plate is the same
    p2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=pl2.poly.xdir)

    # Check that the plate is placed correctly
    place_r = p2_r.poly.orientation
    assert place_r.xdir.is_equal(place.xdir)
    assert place_r.ydir.is_equal(place.ydir)
    assert place_r.zdir.is_equal(place.normal)
    assert place_r.origin.is_equal(place.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, p2_r.poly.points2d):
        assert p1.is_equal(p2)


def test_flat_xz_plate_solid(place2):
    pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place2)

    occ_faces = list(iter_faces_with_normal(pl2.solid_occ(), pl2.poly.normal, point_in_plane=pl2.poly.origin))
    occ_verts_sh2 = get_points_from_occ_shape(occ_faces[0])
    occ_verts_sh2 = set_list_first_position_elem(occ_verts_sh2, tuple(pl2.poly.origin))

    # Feed the points back to the Plate constructor and assert that the plate is the same
    pl2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=(1, 0, 0))

    # Check that the plate is placed correctly
    assert pl2_r.poly.xdir.is_equal(pl2.poly.xdir)
    assert pl2_r.poly.ydir.is_equal(pl2.poly.ydir)
    assert pl2_r.poly.normal.is_equal(pl2.poly.normal)
    assert pl2_r.poly.origin.is_equal(pl2.poly.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, pl2_r.poly.points2d):
        assert p1.is_equal(p2)

    nodes = {tuple(n.p) for n in pl2.nodes}
    assert set(occ_verts_sh2) == nodes

    segment_points = set()
    for seg in pl2.poly.segments3d:
        segment_points.add(tuple(seg.p1))
        segment_points.add(tuple(seg.p2))

    assert nodes == segment_points


def test_flat_yz_plate_solid(place3):
    pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place3)

    occ_faces = list(iter_faces_with_normal(pl2.solid_occ(), pl2.poly.normal, point_in_plane=pl2.poly.origin))
    occ_verts_sh2 = get_points_from_occ_shape(occ_faces[0])
    occ_verts_sh2 = set_list_first_position_elem(occ_verts_sh2, tuple(pl2.poly.origin))

    # Feed the points back to the Plate constructor and assert that the plate is the same
    pl2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=(0, 1, 0))

    # Check that the plate is placed correctly
    assert pl2_r.poly.xdir.is_equal(pl2.poly.xdir)
    assert pl2_r.poly.ydir.is_equal(pl2.poly.ydir)
    assert pl2_r.poly.normal.is_equal(pl2.poly.normal)
    assert pl2_r.poly.origin.is_equal(pl2.poly.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, pl2_r.poly.points2d):
        assert p1.is_equal(p2)

    nodes = {tuple(n.p) for n in pl2.nodes}
    assert set(occ_verts_sh2) == nodes

    segment_points = set()
    for seg in pl2.poly.segments3d:
        segment_points.add(tuple(seg.p1))
        segment_points.add(tuple(seg.p2))

    assert nodes == segment_points


def test_flat_xy_offset_plate_shell(place1):
    place1.origin = (0, 0, 1)
    pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place1)
    place = pl2.poly.orientation

    occ_verts_sh2 = get_points_from_occ_shape(pl2.shell_occ())
    occ_verts_sh2 = set_list_first_position_elem(occ_verts_sh2, tuple(place1.origin))

    # Feed the points back to the Plate constructor and assert that the plate is the same
    p2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=place1.xdir)

    # Check that the plate is placed correctly
    place_r = p2_r.poly.orientation
    assert place_r.xdir.is_equal(place.xdir)
    assert place_r.ydir.is_equal(place.ydir)
    assert place_r.zdir.is_equal(place.zdir)
    assert place_r.origin.is_equal(place.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, p2_r.poly.points2d):
        assert p1.is_equal(p2)


def test_flat_xz_offset_plate_shell(place2):
    place2.origin = (0, 0, 3)
    pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place2)
    place = pl2.poly.orientation

    occ_verts_sh2 = get_points_from_occ_shape(pl2.shell_occ())
    occ_verts_sh2 = set_list_first_position_elem(occ_verts_sh2, tuple(place2.origin))

    # Feed the points back to the Plate constructor and assert that the plate is the same
    p2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=place2.xdir)

    # Check that the plate is placed correctly
    place_r = p2_r.poly.orientation
    assert place_r.xdir.is_equal(place.xdir)
    assert place_r.ydir.is_equal(place.ydir)
    assert place_r.zdir.is_equal(place.zdir)
    assert place_r.origin.is_equal(place.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, p2_r.poly.points2d):
        assert p1.is_equal(p2)


def test_oriented_plate():
    # pl2 = ada.Plate("MyPl2", [(0, 0), (0, 5), (5, 5), (5, 0)], 20e-3, orientation=place2)
    pl2 = ada.Plate(
        "pl3", [(0, 0), (0, 1), (1, 1), (1, 0)], 0.01, origin=(4, 0, 4), normal=(0, -1, 0), xdir=(1, 0, 0), color="red"
    )
    place = pl2.poly.orientation

    occ_verts_sh2 = get_points_from_occ_shape(pl2.shell_occ())
    occ_verts_sh2 = set_list_first_position_elem(occ_verts_sh2, tuple(place.origin))

    # Feed the points back to the Plate constructor and assert that the plate is the same
    p2_r = ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3, xdir=place.xdir)

    # Check that the plate is placed correctly
    place_r = p2_r.poly.orientation
    assert place_r.xdir.is_equal(place.xdir)
    assert place_r.ydir.is_equal(place.ydir)
    assert place_r.zdir.is_equal(place.zdir)
    assert place_r.origin.is_equal(place.origin)

    # Check the individual points
    for p1, p2 in zip(pl2.poly.points2d, p2_r.poly.points2d):
        assert p1.is_equal(p2)


def test_roundtrip_fillets(place1, place2):
    a = ada.Assembly("ExportedPlates")
    p = a.add_part(ada.Part("MyPart"))
    pl1 = p.add_plate(ada.Plate("MyPl", [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)], 20e-3, orientation=place1))
    pl2 = p.add_plate(ada.Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, orientation=place2))

    pl1.solid_geom()
    pl1.shell_geom()
    pl2.solid_geom()
    pl2.shell_geom()

    pl1.solid_occ()
    pl2.solid_occ()

    occ_geo1_sh = pl1.shell_occ()
    occ_geo2_sh = pl2.shell_occ()

    occ_verts_sh1 = get_points_from_occ_shape(occ_geo1_sh)
    occ_verts_sh2 = get_points_from_occ_shape(occ_geo2_sh)
    ada.Plate.from_3d_points("MyPl", occ_verts_sh1, 20e-3)
    ada.Plate.from_3d_points("MyPl2", occ_verts_sh2, 20e-3)
    fp = a.to_ifc(file_obj_only=True)

    validate(fp, logging)

    b = ada.from_ifc(fp)
    _ = b.to_ifc(file_obj_only=True)


def test_2ifc_simple(place2):
    pl2 = ada.Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, orientation=place2)
    seg_list = pl2.poly.segments
    seg1 = seg_list[0]
    assert len(pl2.poly.segments) == 6
    assert type(seg1) is ada.ArcSegment

    # (ada.Assembly("ExportedPlates") / (ada.Part("MyPart") / pl2)).to_ifc(test_dir / "my_plate_poly.ifc")


def test_triangle(tmp_path):
    points2d = [(0, 0), (1, 0, 0.1), (0.5, 0.5)]
    orientation = Placement()
    pl = ada.Plate("test", points2d, 20e-3, orientation=orientation)
    plates = [pl]

    a = ada.Assembly() / [ada.Part("te") / plates]
    a.to_ifc(tmp_path / "triangle_plates_no_rot.ifc", validate=True)

    assert len(pl.poly.segments) == 4
    geom = pl.solid_geom()
    assert isinstance(geom.geometry, geo_so.ExtrudedAreaSolid)

    for alpha in (30, 60, 90):
        new_orientation = orientation.rotate([1, 0, 0], alpha)
        pl.parent.add_plate(ada.Plate(f"rot{alpha}", points2d, 20e-3, orientation=new_orientation))

    # a.to_stp(test_dir / "triangle_plates.stp")
    # a.to_ifc(test_dir / "triangle_plates_rot.ifc", validate=True)


def test_floaty_input_ex1():
    origin = np.array([362237.0037951513, 100000.0, 560985.9095763591])
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
    thick = 30.0

    pl = ada.Plate("test", local_points2d, thick, origin=origin, normal=csys[2], xdir=csys[0], units="mm")

    # a = ada.Assembly(units="mm") / pl
    # a.units = "m"
    # a.to_ifc(test_dir / "error_plate.ifc")

    assert pl.poly.origin == pytest.approx(origin)
    assert pl.poly.normal == pytest.approx(csys[2])


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
    pl = ada.Plate("test2", local_points2d, thick, origin=origin, normal=csys[2], xdir=csys[0], units="mm")
    assert pl.poly.origin == pytest.approx(origin)
    # a = (ada.Assembly(units="mm") / [ada.Part("te", units="mm") / pl]).to_ifc(test_dir / "error_plate2.ifc")
