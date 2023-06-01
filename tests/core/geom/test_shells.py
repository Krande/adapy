from OCC.Core.TopoDS import TopoDS_Compound

from ada import Beam
from ada.occ.geom import geom_to_occ_geom
import math


def test_ipe_beam():
    bm_xyz = Beam("my_beam_x", (0, 0, 0), (1, 0, 1), "IPE300")

    geo_xyz = bm_xyz.shell_geom()

    h = bm_xyz.section.h
    w = bm_xyz.section.w_top
    alpha = math.atan(1 / 1)
    assert math.degrees(alpha) == 45.0
    y_o = h / 2
    x = y_o * math.sin(alpha)
    y = y_o * math.cos(alpha)

    # Check face1
    face1 = geo_xyz.geometry.fbsm_faces[0].cfs_faces[0]
    assert len(face1.bound.polygon) == 4

    p1 = face1.bound.polygon[0]

    topo_ds = geom_to_occ_geom(geo_xyz)
    assert isinstance(topo_ds, TopoDS_Compound)
