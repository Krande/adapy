import ada


def test_read_shape_w_transformation(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/mapped_shapes/mapped-shape-with-transformation.ifc")
    _ = a.to_ifc(ifc_test_dir / "mapped-shape-with-transformation.ifc", return_file_obj=True)
    print(a)
