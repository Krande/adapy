"""Node relations written into an IFC: a system of plain members, connections, a filled opening, and
the property set that keeps a relation whose other side is not in the file."""

import ifcopenshell.validate
import pytest

import ada
from ada.assets.relation_lookup import StaticRelationLookup, apply_relations
from ada.base.ifc_types import IFC_ENTITY_KEY, ifc_entity_spec
from ada.cadit.ifc.write.write_relations import PSET_NAME, RELATIONS_KEY

SCHEMAS = ["IFC4", "IFC4X3_add2"]

PIECE = "/PIECE-1"
RUN = "/RUN-1"
PLATE = "/PLATE-1"
OPENING = "/PIECE-1-OPENING"
SYSTEM = "/SYS-1"

ROWS = [
    {"from_node_id": "n-piece", "from_full_name": PIECE, "relation": "serves_system", "to_node_id": "n-sys",
     "to_full_name": SYSTEM, "props": {"system_kind": "duct"}},
    {"from_node_id": "n-piece", "from_full_name": PIECE, "relation": "connects_to", "to_node_id": "n-run",
     "to_full_name": RUN},
    {"from_node_id": "n-piece", "from_full_name": PIECE, "relation": "fills_opening", "to_full_name": OPENING,
     "props": {"host_full_name": PLATE}},
    {"from_node_id": "n-piece", "from_full_name": PIECE, "relation": "penetrates", "to_node_id": "n-plate",
     "to_full_name": PLATE},
]  # fmt: skip


def _element(name: str, x: float, spec: dict | None):
    part = ada.Part(name)
    if spec is not None:
        part.metadata[IFC_ENTITY_KEY] = spec
    part.add_shape(ada.PrimBox(f"{name} body", (x, 0, 0), (x + 1, 1, 1)))
    return part


def _model(schema: str, *, with_run: bool = False, with_plate: bool = False) -> ada.Assembly:
    a = ada.Assembly("a", schema=schema)
    top = ada.Part("top")
    a.add_part(top)
    top.add_part(_element(PIECE, 0, ifc_entity_spec("IfcDuctFitting", "PENETRATIONSLEEVE")))
    if with_run:
        top.add_part(_element(RUN, 2, ifc_entity_spec("IfcDuctSegment", "RIGIDSEGMENT")))
    if with_plate:
        top.add_part(_element(PLATE, 4, ifc_entity_spec("IfcPlate")))
    return a


def _write(schema: str, **kw):
    a = _model(schema, **kw)
    lookup = StaticRelationLookup({"col": ROWS})
    attached, token = apply_relations(a, lookup, ["col"])
    assert attached == 4 and token == "static"
    assert len(lookup.calls) == 1, "one lookup per collection"
    f = a.to_ifc(file_obj_only=True)
    errors = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, errors)
    assert not errors.statements, errors.statements
    (piece,) = [e for e in f.by_type("IfcDuctFitting") if e.Name == PIECE]
    return f, piece


def _pset(piece) -> dict:
    for rel in piece.IsDefinedBy:
        pset = rel.RelatingPropertyDefinition
        if pset.is_a("IfcPropertySet") and pset.Name == PSET_NAME:
            return {p.Name: p.NominalValue.wrappedValue for p in pset.HasProperties}
    return {}


@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_piece_exported_alone_still_names_its_system_and_keeps_its_relations(schema):
    f, piece = _write(schema)
    (system,) = f.by_type("IfcDistributionSystem")
    assert (system.Name, system.PredefinedType) == (SYSTEM, "VENTILATION")
    (group,) = [r for r in f.by_type("IfcRelAssignsToGroup") if r.RelatingGroup == system]
    assert list(group.RelatedObjects) == [piece]
    assert not f.by_type("IfcRelConnectsElements") and not f.by_type("IfcRelFillsElement")
    props = _pset(piece)
    assert props["connects_to"] == RUN and props["connects_to_node_id"] == "n-run"
    assert props["fills_opening"] == OPENING and props["fills_opening_host"] == PLATE
    assert props["penetrates"] == PLATE
    assert not [p for p in f.by_type("IfcPropertySet") if p.Name == RELATIONS_KEY], "the key is no property"


@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_piece_with_its_run_is_connected_to_it(schema):
    f, piece = _write(schema, with_run=True)
    (rel,) = f.by_type("IfcRelConnectsElements")
    assert rel.RelatingElement == piece and rel.RelatedElement.Name == RUN
    assert "connects_to" not in _pset(piece)


@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_piece_with_its_host_fills_an_opening_in_it(schema):
    f, piece = _write(schema, with_plate=True)
    (fills,) = f.by_type("IfcRelFillsElement")
    assert fills.RelatedBuildingElement == piece
    opening = fills.RelatingOpeningElement
    assert opening.Name == OPENING
    (voids,) = f.by_type("IfcRelVoidsElement")
    assert voids.RelatedOpeningElement == opening and voids.RelatingBuildingElement.Name == PLATE
    assert not f.by_type("IfcRelConnectsElements"), "the opening says the penetration already"
    assert "fills_opening" not in _pset(piece) and "penetrates" not in _pset(piece)


def test_the_system_of_plain_members_reads_back():
    """The reader rebuilds systems from IfcDistributionSystem groupings; one whose members are not
    pipe segments must not stop the read."""
    import tempfile
    from pathlib import Path

    a = _model("IFC4", with_run=True)
    apply_relations(a, StaticRelationLookup({"col": ROWS}), ["col"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "relations.ifc"
        a.to_ifc(path)
        back = ada.from_ifc(path)
    names = {p.name for p in back.get_all_parts_in_assembly()} | {o.name for o in back.get_all_physical_objects()}
    assert PIECE in names or f"{PIECE} body" in names


def test_without_a_lookup_nothing_is_attached():
    a = _model("IFC4")
    assert apply_relations(a, None, ["col"]) == (0, None)
    assert not a.systems
