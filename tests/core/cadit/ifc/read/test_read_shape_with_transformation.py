import ada


def test_read_shape_w_transformation(example_files):
    a = ada.from_ifc(example_files / "ifc_files/mapped_shapes/mapped-shape-with-transformation.ifc")
    _ = a.to_ifc(file_obj_only=True)
    print(a)
