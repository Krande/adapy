import ada
from ada.config import Config


def test_read_shape_w_transformation(example_files):
    a = ada.from_ifc(example_files / "ifc_files/mapped_shapes/mapped-shape-with-transformation.ifc")
    _ = a.to_ifc(file_obj_only=True)
    print(a)

def test_read_rotated_box(example_files):
    Config().update_config_globally("ifc_import_shape_geom",True)
    a = ada.from_ifc(example_files / "ifc_files/box_rotated.ifc")
    door1 = a.get_by_name('door1')
    door2 = a.get_by_name('door2')
    print(a)
