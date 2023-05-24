from OCC.Core.TopoDS import TopoDS_Solid
from ada.concepts.stru_beams import Beam

from ada.geom.solids import ExtrudedAreaSolid
from ada.geom.surfaces import ArbitraryProfileDefWithVoids
from ada.occ.geom.solids import make_extruded_area_solid_from_geom


def test_ipe_beam():
    bm_x = Beam("my_beam_x", (0, 0, 0), (1, 0, 0), "IPE300")
    bm_y = Beam("my_beam_y", (0, 0, 0), (0, 1, 0), "IPE300")
    bm_z = Beam("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE300")
    bm_xyz = Beam("my_beam_xyz", (0, 0, 0), (1, 1, 1), "IPE300")

    geo_x = bm_x.solid_geom()

    # Z-Direction
    geo_z = bm_z.solid_geom()
    assert isinstance(geo_z.geometry, ExtrudedAreaSolid)
    assert geo_z.geometry.depth == 1.0
    assert isinstance(geo_z.geometry.swept_area, ArbitraryProfileDefWithVoids)

    occ_shape = make_extruded_area_solid_from_geom(geo_z.geometry)
    assert isinstance(occ_shape, TopoDS_Solid)
