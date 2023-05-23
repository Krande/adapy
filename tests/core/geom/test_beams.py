from ada import Beam
from ada.geom.solids import ExtrudedAreaSolid
from ada.geom.surfaces import ArbitraryProfileDefWithVoids


def test_ipe_beam():
    bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), "IPE300")

    geometry = bm.solid_geom()
    assert isinstance(geometry.geometry, ExtrudedAreaSolid)
    assert geometry.geometry.depth == 1.0
    assert isinstance(geometry.geometry.swept_area, ArbitraryProfileDefWithVoids)
