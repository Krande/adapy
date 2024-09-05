import ada
from ada.config import Config


def test_import_arcboundary(example_files):
    a = ada.from_ifc(example_files / "ifc_files/with_arc_boundary.ifc")
    print(a)


def test_import_bspline_w_knots(example_files):
    Config().ifc_import_shape_geom = True
    a = ada.from_ifc(example_files / "ifc_files/bsplinewknots.ifc")
    print(a)
