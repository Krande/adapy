from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Solid

import ada
import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su
from ada.core.utils import set_list_first_position_elem
from ada.occ.geom import geom_to_occ_geom
from ada.occ.utils import get_points_from_occ_shape, iter_faces_with_normal


def test_cyl():
    cyl = ada.PrimCyl("my_cyl", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cyl.solid_geom()
    assert isinstance(geo.geometry, geo_so.Cylinder)


def test_box():
    box = ada.PrimBox("my_box", (0, 0, 0), (1, 1, 1))
    geo = box.solid_geom()
    assert isinstance(geo.geometry, geo_so.Box)


def test_cone():
    cone = ada.PrimCone("my_cone", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cone.solid_geom()
    assert isinstance(geo.geometry, geo_so.Cone)


def test_sphere(tmp_path):
    sphere = ada.PrimSphere("my_sphere", (0, 0, 0), 1.0)
    geo = sphere.solid_geom()
    assert isinstance(geo.geometry, geo_so.Sphere)

    # (ada.Assembly("a") / sphere).to_ifc(tmp_path / "test_sphere.ifc", validate=True)


def test_ipe_beam():
    bm = ada.Beam("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE300")

    geo = bm.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)
    assert geo.geometry.depth == 1.0
    assert isinstance(geo.geometry.swept_area, geo_su.ArbitraryProfileDef)

    occ_shape = geom_to_occ_geom(geo)
    assert isinstance(occ_shape, TopoDS_Solid)


def test_ipe_beam_taper():
    bm = ada.BeamTapered("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE400", "IPE300")
    _ = ada.Part("my_part") / bm
    assert bm.section.h == 0.4
    assert bm.taper.h == 0.3

    geo = bm.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolidTapered)
    assert geo.geometry.depth == 1.0

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


def test_pipe1():
    po = [ada.Point(1, 1, 3) + x for x in [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)]]
    pipe1 = ada.Pipe("pipe1", po, "PIPE200x5", color="green")

    straight1 = pipe1.segments[0]
    assert isinstance(straight1, ada.PipeSegStraight)
    straight1_geo = straight1.solid_geom()
    assert isinstance(straight1_geo.geometry, geo_so.ExtrudedAreaSolid)

    elbow2 = pipe1.segments[1]
    assert isinstance(elbow2, ada.PipeSegElbow)
    elbow2_geo = elbow2.solid_geom()
    assert isinstance(elbow2_geo.geometry, geo_so.RevolvedAreaSolid)


def test_prim_sweep1(tmp_path):
    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.2),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    profile2d = [(0, 0), (1, 0), (1, 1), (0, 1)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red")
    geom = sweep.solid_geom()

    assert isinstance(geom.geometry, geo_so.FixedReferenceSweptAreaSolid)

    sweep.solid_occ()

    a = ada.Assembly("SweptShapes") / [ada.Part("MyPart") / [sweep]]
    a.to_ifc(tmp_path / "my_swept_shape_m.ifc", file_obj_only=False, validate=True)
