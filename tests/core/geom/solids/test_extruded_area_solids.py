from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Solid

import ada
from ada.core.utils import set_list_first_position_elem
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su
from ada.occ.geom import geom_to_occ_geom
from ada.occ.utils import get_points_from_occ_shape, iter_faces_with_normal


def test_ipe_beam():
    bm = ada.Beam("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE300")

    geo = bm.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)
    assert geo.geometry.depth == 1.0
    assert isinstance(geo.geometry.swept_area, geo_su.ArbitraryProfileDef)

    occ_shape = geom_to_occ_geom(geo)
    assert isinstance(occ_shape, TopoDS_Solid)


def test_plate_xy():
    pl = ada.Plate("pl1", [(0, 0), (1, 0, 0.2), (1, 1), (0, 1)], 0.1, color="red")

    geo = pl.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)

    occ_shape = geom_to_occ_geom(geo)
    assert isinstance(occ_shape, TopoDS_Solid)

    occ_face = list(iter_faces_with_normal(occ_shape, pl.poly.normal, pl.poly.origin))[0]
    assert isinstance(occ_face, TopoDS_Face)

    occ_face_verts = get_points_from_occ_shape(occ_face)
    occ_face_verts.index(tuple(pl.poly.origin))
    occ_face_verts_adjusted = set_list_first_position_elem(occ_face_verts, tuple(pl.poly.origin))
    assert len(occ_face_verts) == 5

    pl_2 = ada.Plate.from_3d_points("pl2", occ_face_verts_adjusted, 0.1, color="red", xdir=pl.poly.xdir)
    assert pl_2.poly.origin.is_equal(pl.poly.origin)
    assert pl_2.poly.xdir.is_equal(pl.poly.xdir)


def test_plate_xy_offset(tmp_path):
    pl = ada.Plate("pl1", [(0, 0), (0, 1), (1, 1), (1, 0)], 0.1, origin=(0, 0, 2), color="red")

    geo = pl.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)

    occ_shape = geom_to_occ_geom(geo)
    assert isinstance(occ_shape, TopoDS_Solid)

    occ_face = list(iter_faces_with_normal(occ_shape, pl.poly.normal, pl.poly.origin))[0]
    assert isinstance(occ_face, TopoDS_Face)

    occ_face_verts = get_points_from_occ_shape(occ_face)
    occ_face_verts.index(tuple(pl.poly.origin))
    occ_face_verts_adjusted = set_list_first_position_elem(occ_face_verts, tuple(pl.poly.origin))
    assert len(occ_face_verts) == 4

    pl_2 = ada.Plate.from_3d_points("pl2", occ_face_verts_adjusted, 0.1, color="red", xdir=pl.poly.xdir)
    assert pl_2.poly.origin.is_equal(pl.poly.origin)
    assert pl_2.poly.xdir.is_equal(pl.poly.xdir)

    # (ada.Assembly("a") / pl_2).to_ifc(tmp_path / "test_plate_xy_offset.ifc", validate=True)


def test_plate_xz():
    pl = ada.Plate("pl1", [(0, 0), (0, 1), (1, 1), (1, 0)], 0.1, origin=(0, 0, 0), n=(0, -1, 0), xdir=(1, 0, 0))

    geo = pl.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)

    occ_shape = geom_to_occ_geom(geo)
    assert isinstance(occ_shape, TopoDS_Solid)

    occ_face = list(iter_faces_with_normal(occ_shape, pl.poly.normal, pl.poly.origin))[0]
    assert isinstance(occ_face, TopoDS_Face)

    occ_face_verts = get_points_from_occ_shape(occ_face)
    occ_face_verts.index(tuple(pl.poly.origin))
    occ_face_verts_adjusted = set_list_first_position_elem(occ_face_verts, tuple(pl.poly.origin))
    assert len(occ_face_verts) == 4

    pl_2 = ada.Plate.from_3d_points("pl2", occ_face_verts_adjusted, 0.1, color="red", xdir=pl.poly.xdir)
    assert pl_2.poly.origin.is_equal(pl.poly.origin)
    assert pl_2.poly.xdir.is_equal(pl.poly.xdir)
