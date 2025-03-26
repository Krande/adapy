import ada
from ada.geom import solids as geo_so


def test_elbow_xy():
    p1 = (1, 0, 0)
    p = (1, 1, 0)
    p2 = (0, 1, 0)
    r = 0.2

    elbow = ada.PipeSegElbow.from_arc_segment(
        "myElbow", ada.ArcSegment.from_start_center_end_radius(p1, p, p2, r), "OD140x10"
    )
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)


def test_elbow_xz():
    p1 = (1, 0, 0)
    p = (1, 0, 1)
    p2 = (0, 0, 1)
    r = 0.2

    elbow = ada.PipeSegElbow.from_arc_segment(
        "myElbow", ada.ArcSegment.from_start_center_end_radius(p1, p, p2, r), "OD140x10"
    )
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)


def test_elbow_yz():

    p = (0, 0, 0)
    p1 = (-1, -1, 0)
    p2 = (0, 1, 1)
    r = 0.2

    elbow = ada.PipeSegElbow.from_arc_segment(
        "myElbow", ada.ArcSegment.from_start_center_end_radius(p1, p, p2, r), "OD140x10"
    )
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)


def test_elbow_out_of_plane():
    p1 = (1, 0, 0)
    center = (1, 1, 1)
    p2 = (0, 1, 1)
    r = 0.2

    elbow = ada.PipeSegElbow("myelbow", p1, center, p2, r, "OD140x10")
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)




def test_revolved_beam():
    bm1_straight = ada.Beam("bm1", (0, 0, 0), (0, 1, 0), "IPE200")
    bm2_rev = ada.BeamRevolve("bm2", ada.CurveRevolve((0, 0, 0), (0, 1, 0), 1.3, (0, 0, 1)), "IPE200")
    # p = ada.Part("myPart") / (bm1_straight, bm2_rev)
    straight_geo = bm1_straight.solid_geom()
    rev_geo = bm2_rev.solid_geom()

    assert isinstance(straight_geo.geometry, geo_so.ExtrudedAreaSolid)
    assert isinstance(rev_geo.geometry, geo_so.RevolvedAreaSolid)
