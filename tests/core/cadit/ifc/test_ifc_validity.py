"""adapy-written IFC must pass schema + EXPRESS where-rule validation (the offline pillars of the
official buildingSMART validation service — see scripts/validate_ifc.py / the ifc-validate task).

Guards two writer bugs the validator caught:
* a beam used to get TWO IfcRelAssociatesMaterial (the bare per-material rel on top of its
  IfcMaterialProfileSetUsage), violating IfcBuiltElement.MaxOneMaterialAssociation;
* an unused material's eagerly-created rel used to keep an empty RelatedObjects set.
"""

from __future__ import annotations

import ifcopenshell
import pytest
from ifcopenshell.validate import json_logger, validate

import ada


def _model():
    p = ada.Part("p")
    p.add_plate(ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.02))
    p.add_beam(ada.Beam("b", (0, 0, 0), (1, 0, 0), "IPE200"))
    return ada.Assembly("A") / p


@pytest.mark.parametrize("streaming", [False, True], ids=["normal", "streaming"])
def test_written_ifc_passes_express_validation(tmp_path, streaming):
    dest = tmp_path / "m.ifc"
    _model().to_ifc(dest, streaming=streaming)

    f = ifcopenshell.open(str(dest))
    lg = json_logger()
    validate(f, lg, express_rules=True)
    assert lg.statements == [], [s.get("message") for s in lg.statements]

    # exactly ONE material association per element: the beam's is its profile-set usage
    for elem in (*f.by_type("IfcBeam"), *f.by_type("IfcPlate")):
        mat_rels = [r for r in elem.HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
        assert len(mat_rels) == 1, f"{elem.is_a()} '{elem.Name}' has {len(mat_rels)} material associations"
    (beam_rel,) = [r for r in f.by_type("IfcBeam")[0].HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
    assert beam_rel.RelatingMaterial.is_a("IfcMaterialProfileSetUsage")


@pytest.mark.parametrize("streaming", [False, True], ids=["normal", "streaming"])
def test_beam_material_roundtrips_via_profile_set_usage(tmp_path, streaming):
    dest = tmp_path / "m.ifc"
    _model().to_ifc(dest, streaming=streaming)
    b = ada.from_ifc(dest).get_by_name("b")
    assert b.material.name == "S355"
    assert b.section.name == "IPE200"
