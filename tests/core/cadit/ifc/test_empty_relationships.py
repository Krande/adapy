"""Exported IFC must not contain member-less relationships.

An unused material (or a member-less group) would otherwise leave an
``IfcRelAssociatesMaterial`` / ``IfcRelAssignsToGroup`` with an empty
``RelatedObjects`` set, violating the schema's ``[1:?]`` cardinality and failing
ifcopenshell validation.
"""

import ada
from ada.materials import Material


def test_no_empty_relationships_and_valid(tmp_path):
    from ifcopenshell import validate

    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), sec="IPE300")
    a = ada.Assembly() / (ada.Part("P") / bm)
    # An extra material that no element uses — the eager IfcRelAssociatesMaterial
    # for it would be empty and must be pruned on export.
    a.add_material(Material("UnusedMat"))

    fp = a.to_ifc(tmp_path / "model.ifc", file_obj_only=True)

    for rel_type in ("IfcRelAssociatesMaterial", "IfcRelAssignsToGroup"):
        empties = [r for r in fp.by_type(rel_type) if not r.RelatedObjects]
        assert empties == [], f"{len(empties)} empty {rel_type} survived export"

    logger = validate.json_logger()
    validate.validate(fp, logger)
    cardinality_issues = [s for s in logger.statements if "RelatedObjects" in (s.get("message") or "")]
    assert cardinality_issues == []
