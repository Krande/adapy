import ada
from ada.geom import solids as geo_so








def test_cone():
    cone = ada.PrimCone("my_cone", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cone.solid_geom()
    assert isinstance(geo.geometry, geo_so.Cone)
