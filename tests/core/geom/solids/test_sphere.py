import ada
from ada.geom import solids as geo_so


def test_sphere(tmp_path):
    sphere = ada.PrimSphere("my_sphere", (0, 0, 0), 1.0)
    geo = sphere.solid_geom()
    assert isinstance(geo.geometry, geo_so.Sphere)

    # (ada.Assembly("a") / sphere).to_ifc(tmp_path / "test_sphere.ifc", validate=True)
