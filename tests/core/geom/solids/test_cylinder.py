import ada
from ada.geom import solids as geo_so

def test_cyl():
    cyl = ada.PrimCyl("my_cyl", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cyl.solid_geom()
    assert isinstance(geo.geometry, geo_so.Cylinder)