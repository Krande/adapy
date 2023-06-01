from OCC.Core.TopoDS import TopoDS_Solid

import ada
import ada.geom.solids as so
from ada.geom.surfaces import ArbitraryProfileDefWithVoids
from ada.occ.geom import geom_to_occ_geom


def test_cyl():
    cyl = ada.PrimCyl("my_cyl", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cyl.solid_geom()
    assert isinstance(geo.geometry, so.Cylinder)


def test_box():
    box = ada.PrimBox("my_box", (0, 0, 0), (1, 1, 1))
    geo = box.solid_geom()
    assert isinstance(geo.geometry, so.Box)


def test_cone():
    cone = ada.PrimCone("my_cone", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cone.solid_geom()
    assert isinstance(geo.geometry, so.Cone)


def test_sphere():
    sphere = ada.PrimSphere("my_sphere", (0, 0, 0), 1.0)
    geo = sphere.solid_geom()
    assert isinstance(geo.geometry, so.Sphere)


def test_ipe_beam():
    bm_z = ada.Beam("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE300")

    # Z-Direction
    geo_z = bm_z.solid_geom()
    assert isinstance(geo_z.geometry, so.ExtrudedAreaSolid)
    assert geo_z.geometry.depth == 1.0
    assert isinstance(geo_z.geometry.swept_area, ArbitraryProfileDefWithVoids)

    occ_shape = geom_to_occ_geom(geo_z)
    assert isinstance(occ_shape, TopoDS_Solid)
