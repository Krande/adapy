from OCC.Core.TopoDS import TopoDS_Shell

from ada.concepts.stru_beams import Beam
from ada.occ.geom.solids import make_extruded_area_shape_from_geom


def test_ipe_beam():
    bm_x = Beam("my_beam_x", (0, 0, 0), (1, 0, 0), "IPE300")

    geo_x = bm_x.shell_geom()

    topo_ds = make_extruded_area_shape_from_geom(geo_x.geometry)
    assert isinstance(topo_ds, TopoDS_Shell)
