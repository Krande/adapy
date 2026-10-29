"""buildingSMART validation rule SPS002 (Correct spatial breakdown), re-implemented.

The rule (ifc-gherkin-rules, features/rules/SPS/SPS002) requires every IfcSpatialElement and the
IfcProject to be aggregated as per ``spatial_CompositionTable.csv``, and the whole of an
aggregation to be of a type that may decompose into the part. The rows relevant to the entities
adapy writes are copied below.
"""

import ifcopenshell
import ifcopenshell.validate
import pytest

import ada
from ada.base.ifc_types import SpatialTypes

# ApplicableEntity -> allowed RelatingObject types (rows of spatial_CompositionTable.csv)
COMPOSITION = {
    "IfcSite": {"IfcProject", "IfcSite"},
    "IfcBuilding": {"IfcProject", "IfcSite", "IfcBuilding"},
    "IfcBuildingStorey": {"IfcBuilding", "IfcBuildingStorey"},
    "IfcExternalSpatialElement": {"IfcProject", "IfcSite", "IfcExternalSpatialElement"},
    "IfcSpace": {
        "IfcProject",
        "IfcSite",
        "IfcBuilding",
        "IfcBuildingStorey",
        "IfcSpace",
        "IfcFacility",
        "IfcBridge",
        "IfcMarineFacility",
        "IfcRailway",
        "IfcRoad",
        "IfcFacilityPart",
        "IfcBridgePart",
        "IfcMarinePart",
        "IfcRailwayPart",
        "IfcRoadPart",
    },
}


def _violations(f: ifcopenshell.file) -> list[str]:
    problems = []
    # forward: the part must be aggregated by an allowed type
    for ent in list(f.by_type("IfcSpatialElement")) + list(f.by_type("IfcProject")):
        allowed = [v for k, v in COMPOSITION.items() if ent.is_a(k)]
        if not allowed:
            continue
        allowed_types = set().union(*allowed)
        rels = ent.Decomposes
        if not rels:
            if not ent.is_a("IfcProject"):
                problems.append(f"{ent.is_a()} not aggregated")
            continue
        whole = rels[0].RelatingObject
        if not any(whole.is_a(t) for t in allowed_types):
            problems.append(f"{ent.is_a()} aggregated by {whole.is_a()}")
    # reverse: every part of an aggregation of a spatial whole must be allowed to be there
    for whole in list(f.by_type("IfcSpatialElement")) + list(f.by_type("IfcProject")):
        if not whole.IsDecomposedBy:
            continue
        for part in whole.IsDecomposedBy[0].RelatedObjects:
            allowed = [v for k, v in COMPOSITION.items() if part.is_a(k)]
            if allowed and not any(whole.is_a(t) for t in set().union(*allowed)):
                problems.append(f"{whole.is_a()} decomposed by {part.is_a()}")
    return problems


def _selection_like_model(schema: str) -> ada.Assembly:
    # the selection export puts the selection under copies of its ancestors: default Parts
    a = ada.Assembly("Selection", schema=schema)
    top = ada.Part("Level A")
    inner = ada.Part("Level B")
    a.add_part(top)
    top.add_part(inner)
    inner.add_beam(ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE200"))
    other = ada.Part("Level C")
    a.add_part(other)
    other.add_beam(ada.Beam("bm2", (0, 0, 1), (1, 0, 1), "IPE200"))
    return a


@pytest.mark.parametrize("schema", ["IFC4", "IFC4X3_ADD2"])
def test_selection_like_export_follows_sps002(tmp_path, schema):
    dest = tmp_path / f"sel_{schema}.ifc"
    _selection_like_model(schema).to_ifc(destination=dest, file_obj_only=False, validate=True)

    f = ifcopenshell.open(str(dest))
    assert f.schema_identifier.upper().replace(".", "_").startswith(schema.split("_")[0])
    assert _violations(f) == []

    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger)
    assert logger.statements == []

    # storeys hang below one implicit building, shared by both top-level parts
    (site,) = f.by_type("IfcSite")
    (building,) = f.by_type("IfcBuilding")
    assert building.Decomposes[0].RelatingObject == site
    assert {s.Name for s in f.by_type("IfcBuildingStorey") if s.Decomposes[0].RelatingObject == building} == {
        "Level A",
        "Level C",
    }


def test_every_spatial_class_follows_sps002(tmp_path):
    for spatial_class in (SpatialTypes.IfcBuildingStorey, SpatialTypes.IfcBuilding, SpatialTypes.IfcSpace):
        dest = tmp_path / f"{spatial_class}.ifc"
        (ada.Assembly() / ada.Part("Placeholder", ifc_class=spatial_class)).to_ifc(
            destination=dest, file_obj_only=False, validate=True
        )
        assert _violations(ifcopenshell.open(str(dest))) == [], spatial_class
