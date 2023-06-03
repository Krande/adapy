from OCC.Core.TopoDS import TopoDS_Solid

import ada
import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su
from ada.occ.geom import geom_to_occ_geom


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


def test_sphere():
    sphere = ada.PrimSphere("my_sphere", (0, 0, 0), 1.0)
    geo = sphere.solid_geom()
    assert isinstance(geo.geometry, geo_so.Sphere)


def test_ipe_beam():
    bm = ada.Beam("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE300")

    geo = bm.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)
    assert geo.geometry.depth == 1.0
    assert isinstance(geo.geometry.swept_area, geo_su.ArbitraryProfileDefWithVoids)

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


def test_plate():
    pl2 = ada.Plate.from_3d_points("my_plate", [(0, 0, 0), (1, 0, 0, 0.2), (1, 1, 0), (0, 1, 0)], 0.1)
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.1, color="red")
    segs = pl.poly.segments
    segs2 = pl2.poly.segments
    geo = pl.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolid)

    occ_shape = geom_to_occ_geom(geo)

    assert isinstance(occ_shape, TopoDS_Solid)
