import ada
from ada.config import Config


def test_basic_box():
    box = ada.PrimBox("box1", (1, 2, 3), (4, 5, 6))
    a = ada.Assembly() / box
    a.ifc_store.sync()
    Config().update_config_globally("ifc_import_shape_geom", True)
    b = ada.from_ifc(a.ifc_store.f)
    results = list(b.get_all_physical_objects())
    assert len(results) == 1
    rshape = results[0]
    assert rshape.name == "box1"
    rbox = ada.PrimBox.from_box_geom(rshape.name, rshape.geom.geometry, metadata=rshape.metadata)
    assert rbox.p1.is_equal(box.p1)
