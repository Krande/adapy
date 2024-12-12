import ada
from ada.config import Config
from ada.geom.solids import Box


def test_read_shape_w_transformation(example_files):
    a = ada.from_ifc(example_files / "ifc_files/mapped_shapes/mapped-shape-with-transformation.ifc")
    _ = a.to_ifc(file_obj_only=True)
    print(a)


def test_read_rotated_box(example_files):
    Config().update_config_globally("ifc_import_shape_geom", True)
    a = ada.from_ifc(example_files / "ifc_files/box_rotated.ifc")
    door1 = a.get_by_name("door1")
    door2 = a.get_by_name("door2")

    geom1 = door1.geom.geometry
    assert isinstance(geom1, Box)

    geom2 = door2.geom.geometry
    assert isinstance(geom2, Box)
