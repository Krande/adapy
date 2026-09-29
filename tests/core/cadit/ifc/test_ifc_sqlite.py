import ada
from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
from ada.cadit.ifc.sql_model import IfcSqlModel
from ada.config import logger


def test_ifc_to_sqlite_roundtrip(tmp_path):
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("MyAssembly") / (ada.Part("MyPart") / bm)
    ifc_fp = tmp_path / "model.ifc"
    sql_fp = tmp_path / "model.sqlite"
    a.to_ifc(ifc_fp)

    Ifc2SqlPatcher(ifc_fp, logger, dest_sql_file=sql_fp).patch()
    store = IfcSqlModel(sql_fp)
    try:
        beams = store.by_type("IfcBeam")
        assert len(beams) == 1
        ifc_beam = beams[0]

        # sqlite_entity is not an ifcopenshell.entity_instance subclass (it can't be on
        # ifcopenshell >= 0.9), so schema queries must still go through its shadow instance.
        assert ifc_beam
        assert ifc_beam.is_a("IfcBeam")
        assert ifc_beam.is_a("IfcProduct")
        assert ifc_beam.Name == "bm1"

        # forward reference resolved to another sqlite_entity
        assert ifc_beam.ObjectPlacement.is_a("IfcLocalPlacement")
        # inverse attribute walks back through the id map
        assert any(rel.is_a("IfcRelContainedInSpatialStructure") for rel in ifc_beam.ContainedInStructure)
    finally:
        store.db.close()
