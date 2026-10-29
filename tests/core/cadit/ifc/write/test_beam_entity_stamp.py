"""A beam a provider typed as another element -- a sleeve drawn as a section -- is written as that
element, its profile and material kept, and without a beam type."""

import ifcopenshell.validate
import pytest

import ada
from ada.base.ifc_types import IFC_ENTITY_KEY, ifc_entity_spec

SCHEMAS = ["IFC4", "IFC4X3_add2"]


def _model(schema: str, spec: dict | None) -> ada.Assembly:
    a = ada.Assembly("a", schema=schema)
    p = ada.Part("top")
    a.add_part(p)
    bm = ada.Beam("/SLEEVE-1", (0, 0, 0), (0, 0, 0.5), "IPE300")
    if spec is not None:
        bm.metadata[IFC_ENTITY_KEY] = spec
    p.add_beam(bm)
    return a


@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_stamped_beam_is_the_stamped_element(schema):
    f = _model(schema, ifc_entity_spec("IfcPipeFitting", "PENETRATIONSLEEVE")).to_ifc(file_obj_only=True)
    errors = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, errors)
    assert not errors.statements, errors.statements
    assert not f.by_type("IfcBeam")
    (sleeve,) = f.by_type("IfcPipeFitting")
    assert (sleeve.Name, sleeve.PredefinedType, sleeve.ObjectType) == ("/SLEEVE-1", "USERDEFINED", "PENETRATIONSLEEVE")
    assert not [r for r in f.by_type("IfcRelDefinesByType") if sleeve in r.RelatedObjects]
    assert sleeve.Representation is not None and sleeve.HasAssociations, "geometry and material kept"


def test_an_unstamped_beam_is_still_a_beam_with_its_type():
    f = _model("IFC4", None).to_ifc(file_obj_only=True)
    (beam,) = f.by_type("IfcBeam")
    assert beam.ObjectType == "Beam"
    assert [r for r in f.by_type("IfcRelDefinesByType") if beam in r.RelatedObjects]
