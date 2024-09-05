import ada
from ada.config import Config


def test_import_arcboundary(example_files):
    a = ada.from_ifc(example_files / "ifc_files/with_arc_boundary.ifc")
    print(a)


def test_import_bspline_w_knots(example_files, monkeypatch):
    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    Config().reload_config()
    a = ada.from_ifc(example_files / "ifc_files/bsplinewknots.ifc")
    print(a)
