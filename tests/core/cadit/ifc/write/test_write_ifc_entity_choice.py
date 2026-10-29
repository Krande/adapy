"""An object's ``IFC_ENTITY_KEY`` decides the IFC entity it is written as, per schema."""

import ifcopenshell.validate
import pytest

import ada
from ada.base.ifc_types import IFC_ENTITY_KEY, ifc_entity_spec

SCHEMAS = ["IFC4", "IFC4X3_add2"]


def _model(schema: str, part_spec: dict | None = None, shape_spec: dict | None = None) -> ada.Assembly:
    a = ada.Assembly("a", schema=schema)
    run = ada.Part("run")
    a.add_part(run)
    fitting = ada.Part("fitting")
    if part_spec is not None:
        fitting.metadata[IFC_ENTITY_KEY] = part_spec
    run.add_part(fitting)
    fitting.add_shape(ada.PrimCyl("body", (0, 0, 0), (0, 0, 1), 0.1))
    loose = ada.PrimBox("loose", (2, 0, 0), (3, 1, 1))
    if shape_spec is not None:
        loose.metadata[IFC_ENTITY_KEY] = shape_spec
    run.add_shape(loose)
    return a


@pytest.mark.parametrize("schema", SCHEMAS)
def test_part_is_written_as_the_element_it_names(schema):
    f = _model(schema, part_spec=ifc_entity_spec("IfcDuctFitting", "BEND")).to_ifc(file_obj_only=True, validate=True)

    (fitting,) = f.by_type("IfcDuctFitting")
    assert fitting.Name == "fitting"
    assert fitting.PredefinedType == "BEND"
    assert not [s for s in f.by_type("IfcBuildingStorey") if s.Name == "fitting"]
    # Contained in its spatial parent, its body aggregated into it.
    (contained,) = fitting.ContainedInStructure
    assert contained.RelatingStructure.Name == "run"
    (agg,) = fitting.IsDecomposedBy
    assert [o.Name for o in agg.RelatedObjects] == ["body"]
    # The key is not written as a property set.
    assert not [p for p in f.by_type("IfcPropertySet") if p.Name == IFC_ENTITY_KEY]
    errors = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, errors)
    assert not errors.statements


@pytest.mark.parametrize("schema", SCHEMAS)
def test_shape_is_written_as_the_element_it_names(schema):
    f = _model(schema, shape_spec=ifc_entity_spec("IfcDuctSegment", "RIGIDSEGMENT")).to_ifc(file_obj_only=True)
    (seg,) = f.by_type("IfcDuctSegment")
    assert (seg.Name, seg.PredefinedType) == ("loose", "RIGIDSEGMENT")


def test_unstamped_objects_keep_the_default():
    f = _model("IFC4X3_add2").to_ifc(file_obj_only=True)
    assert {p.Name for p in f.by_type("IfcBuildingElementProxy")} == {"body", "loose"}
    assert "fitting" in {s.Name for s in f.by_type("IfcBuildingStorey")}


def test_unknown_or_non_element_entity_falls_back():
    f = _model("IFC4", part_spec={"entity": "IfcNotAThing"}, shape_spec={"entity": "IfcBuildingStorey"}).to_ifc(
        file_obj_only=True
    )
    assert "fitting" in {s.Name for s in f.by_type("IfcBuildingStorey")}
    assert "loose" in {p.Name for p in f.by_type("IfcBuildingElementProxy")}


def test_unlisted_predefined_type_is_userdefined():
    f = _model("IFC4", shape_spec=ifc_entity_spec("IfcDuctFitting", "SPLITTER")).to_ifc(file_obj_only=True)
    (fit,) = f.by_type("IfcDuctFitting")
    assert (fit.PredefinedType, fit.ObjectType) == ("USERDEFINED", "SPLITTER")


@pytest.mark.parametrize(
    "schema, expected",
    [("IFC4", ("IfcDuctFitting", "TRANSITION")), ("IFC4X3_add2", ("IfcDuctFitting", "JUNCTION"))],
)
def test_per_schema_override(schema, expected):
    spec = ifc_entity_spec("IfcDuctFitting", "JUNCTION", schemas={"IFC4": {"predefined_type": "TRANSITION"}})
    f = _model(schema, shape_spec=spec).to_ifc(file_obj_only=True)
    (fit,) = f.by_type(expected[0])
    assert fit.PredefinedType == expected[1]


@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_part_inside_an_element_is_written_as_an_assembly(schema):
    a = ada.Assembly("a", schema=schema)
    run = ada.Part("run")
    a.add_part(run)
    unit = ada.Part("unit")
    unit.metadata[IFC_ENTITY_KEY] = ifc_entity_spec("IfcUnitaryEquipment", "AIRHANDLER")
    run.add_part(unit)
    section = ada.Part("section")
    unit.add_part(section)
    section.add_shape(ada.PrimBox("casing", (0, 0, 0), (1, 1, 1)))
    f = a.to_ifc(file_obj_only=True)

    (eq,) = f.by_type("IfcUnitaryEquipment")
    (asm,) = f.by_type("IfcElementAssembly")
    assert asm.Name == "section" and asm.Decomposes[0].RelatingObject == eq
    assert [o.Name for o in asm.IsDecomposedBy[0].RelatedObjects] == ["casing"]
    assert "section" not in {s.Name for s in f.by_type("IfcBuildingStorey")}
    errors = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, errors)
    assert not errors.statements


def test_spec_refuses_malformed_input():
    with pytest.raises(ValueError):
        ifc_entity_spec("")
    with pytest.raises(ValueError):
        ifc_entity_spec("IfcDuctFitting", schemas={"IFC4": {"colour": "red"}})
