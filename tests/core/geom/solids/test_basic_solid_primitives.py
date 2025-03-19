import ada
from ada.geom import solids as geo_so


def test_cyl():
    cyl = ada.PrimCyl("my_cyl", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cyl.solid_geom()
    assert isinstance(geo.geometry, geo_so.Cylinder)


def test_box():
    box = ada.PrimBox("my_box", (0, 0, 0), (1, 1, 1))
    geo = box.solid_geom()
    assert isinstance(geo.geometry, geo_so.Box)


def test_cone():
    cone = ada.PrimCone("my_cone", (0, 0, 0), (0, 0, 1), 1.0)
    geo = cone.solid_geom()
    assert isinstance(geo.geometry, geo_so.Cone)


def test_sphere(tmp_path):
    sphere = ada.PrimSphere("my_sphere", (0, 0, 0), 1.0)
    geo = sphere.solid_geom()
    assert isinstance(geo.geometry, geo_so.Sphere)

    # (ada.Assembly("a") / sphere).to_ifc(tmp_path / "test_sphere.ifc", validate=True)
