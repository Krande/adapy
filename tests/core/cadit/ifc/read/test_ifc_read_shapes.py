import ada
from ada.geom.surfaces import AdvancedFace


def test_import_arc_boundary(example_files, monkeypatch):
    # monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    # Config().reload_config()
    a = ada.from_ifc(example_files / "ifc_files/with_arc_boundary.ifc")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    shape = objects[0]

    assert shape is not None


def test_import_bspline_w_knots(example_files, monkeypatch, tmp_path):
    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    a = ada.from_ifc(example_files / "ifc_files/bsplinesurfacewithknots.ifc")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1

    shape = objects[0]
    assert shape.geom is not None

    geom = shape.geom

    assert isinstance(geom.geometry, AdvancedFace)

    b = ada.Assembly()
    p = b.add_part(ada.Part("MyPart"))
    p.add_material(shape.material.copy_to("S355", p))
    p.add_shape(shape)
    b.to_ifc(tmp_path / "bsplinesurfacewithknots.ifc", validate=True)
