from OCC.Core.TopoDS import TopoDS_Solid

import ada
from ada.geom import solids as geo_so
from ada.occ.geom import geom_to_occ_geom


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
