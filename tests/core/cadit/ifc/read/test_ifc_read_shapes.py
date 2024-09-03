import ada


def test_import_arcboundary(example_files):
    a = ada.from_ifc(example_files / "ifc_files/with_arc_boundary.ifc")
    print(a)
