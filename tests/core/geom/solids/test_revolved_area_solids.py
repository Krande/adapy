import ada
from ada.geom import solids as geo_so


def test_elbow_xy():
    p1 = (1, 0, 0)
    p = (1, 1, 0)
    p2 = (0, 1, 0)
    r = 0.2

    elbow = ada.PipeSegElbow.from_arc_segment("myElbow", ada.ArcSegment.from_start_center_end_radius(p1, p, p2, r), "OD140x10")
    # elbow.show(stream_from_ifc_store=True)
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)


def test_elbow_xz():
    p1 = (1, 0, 0)
    p = (1, 0, 1)
    p2 = (0, 0, 1)
    r = 0.2

    elbow = ada.PipeSegElbow.from_arc_segment("myElbow", ada.ArcSegment.from_start_center_end_radius(p1, p, p2, r), "OD140x10")
    elbow.show(stream_from_ifc_store=True)
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)


def test_elbow_yz():

    p = (0, 0, 0)
    p1 = (-1, -1, 0)
    p2 = (0, 1, 1)
    r = 0.2

    elbow = ada.PipeSegElbow.from_arc_segment("myElbow", ada.ArcSegment.from_start_center_end_radius(p1, p, p2, r), "OD140x10")
    elbow.show(stream_from_ifc_store=True)
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)


def test_elbow_out_of_plane():
    p1 = (1, 0, 0)
    center = (1, 1, 1)
    p2 = (0, 1, 1)
    r = 0.2

    elbow = ada.PipeSegElbow("myelbow", p1, center, p2, r, "OD140x10")
    elbow.show(stream_from_ifc_store=True)
    so_geo = elbow.solid_geom()
    assert isinstance(so_geo.geometry, geo_so.RevolvedAreaSolid)
