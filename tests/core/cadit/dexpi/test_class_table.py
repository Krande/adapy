"""The vendored DEXPI class table and the unit/attribute helpers built on it.

``is_a`` is the load-bearing piece: everything downstream classifies a DEXPI item by supertype
rather than by enumerating class names, so the supertype chains here are asserted explicitly.
"""

from __future__ import annotations

import pytest

from ada.cadit.dexpi import attributes, class_table, units


def test_meta_records_provenance():
    meta = class_table.meta()
    assert meta["source"] == "https://gitlab.com/dexpi/Specification"
    assert meta["tag"] == "V2.0.0"
    assert meta["license"] == "CC BY 4.0"
    assert len(meta["commit"]) == 40
    assert "do not edit" in meta["note"]


def test_table_covers_the_plant_model():
    names = class_table.class_names()
    assert len(names) == class_table.meta()["class_count"]

    packages = {class_table.get(name)["package"] for name in names}
    assert {"Plant/ProcessEquipment", "Plant/Piping", "Plant/Instrumentation", "Core"} <= packages

    process = [n for n in names if class_table.get(n)["package"] == "Plant/ProcessEquipment"]
    piping = [n for n in names if class_table.get(n)["package"] == "Plant/Piping"]
    assert len(process) == 142
    assert len(piping) == 74


@pytest.mark.parametrize(
    "reference, expected",
    [
        ("Plant/Piping.BallValve", "BallValve"),
        ("Plant/PlantModel", "PlantModel"),
        ("Core/EngineeringModel", "EngineeringModel"),
        ("BallValve", "BallValve"),
        ("  Plant/ProcessEquipment.Nozzle  ", "Nozzle"),
        ("https://data.dexpi.org/models/2.0.0/Plant.xml#Plant/Piping.BallValve", "BallValve"),
    ],
)
def test_resolve_accepts_every_wire_form(reference, expected):
    assert class_table.resolve(reference) == expected


def test_get_by_simple_and_qualified_name():
    entry = class_table.get("Plant/Piping.BallValve")
    assert entry == class_table.get("BallValve")
    assert entry["package"] == "Plant/Piping"
    assert entry["abstract"] is False
    assert entry["supertypes"] == ["Plant/Piping.OperatedValve"]
    assert entry["rdl"] == "JORD_RDL.BALL_VALVE"


def test_get_unknown_class_is_none():
    assert class_table.get("NoSuchVendorClass") is None
    assert class_table.supertypes("NoSuchVendorClass") == []


@pytest.mark.parametrize(
    "name, supertype",
    [
        ("BallValve", "BallValve"),  # reflexive
        ("BallValve", "OperatedValve"),  # direct
        ("BallValve", "PipingComponent"),  # two hops
        ("BallValve", "Plant/Piping.PipingComponent"),  # supertype may be qualified too
        ("Plant/Piping.BallValve", "PipingComponent"),  # so may the subject
        ("CentrifugalPump", "ProcessEquipment"),
        ("CentrifugalPump", "TaggedPlantItem"),  # four hops, through ProcessEquipment
        ("Tank", "Vessel"),
        ("Nozzle", "PipingNodeOwner"),  # multiple inheritance, second parent
        ("Nozzle", "SensingLocation"),
    ],
)
def test_is_a_walks_the_supertype_graph(name, supertype):
    assert class_table.is_a(name, supertype) is True


@pytest.mark.parametrize(
    "name, supertype",
    [
        ("BallValve", "CentrifugalPump"),
        ("OperatedValve", "BallValve"),  # not upside down
        ("Tank", "PipingComponent"),
        ("NoSuchVendorClass", "PipingComponent"),  # unknown answers False, never raises
        ("BallValve", "NoSuchVendorClass"),
    ],
)
def test_is_a_rejects_unrelated_classes(name, supertype):
    assert class_table.is_a(name, supertype) is False


def test_class_uri_is_model_scoped():
    expected = "https://data.dexpi.org/models/2.0.0/Plant.xml#Plant/Piping.BallValve"
    assert class_table.class_uri("BallValve") == expected
    assert class_table.class_uri("EngineeringModel").endswith("Core.xml#Core/EngineeringModel")
    assert class_table.class_uri("NoSuchVendorClass") is None


@pytest.mark.parametrize(
    "unit, expected",
    [
        ("Core/PhysicalQuantities.LengthUnit.Millimetre", "millimetre"),
        ("http://data.posccaesar.org/rdl/Millimetre", "millimetre"),
        ("Millimetre", "millimetre"),
        ("mm", "millimetre"),
        ("Meter", "metre"),
        ("m3/h", "metrecubedperhour"),
        ("kg/s", "kilogrampersecond"),
        ("degC", "degreecelsius"),
        ("Furlong", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_unit(unit, expected):
    assert units.normalize_unit(unit) == expected


def test_to_si_converts_and_offsets():
    assert units.to_si(1000.0, "Millimetre") == pytest.approx(1.0)
    assert units.mm_to_m(2500.0) == pytest.approx(2.5)
    assert units.to_si(2.0, "Bar") == pytest.approx(2e5)
    assert units.to_si(20.0, "DegreeCelsius") == pytest.approx(293.15)
    assert units.to_si(1.0, "Furlong") is None
    assert units.si_factor("Inch") == pytest.approx(0.0254)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("DN 50", 0.05),
        ("DN50", 0.05),
        ("100", 0.1),
        ("NPS 2", 0.05),  # NPS 2 is DN 50, not 50.8 mm
        ('6"', 0.15),
        ('1 1/2"', 0.04),
        ("", None),
        (None, None),
    ],
)
def test_parse_nominal_diameter(text, expected):
    result = units.parse_nominal_diameter(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


class _Attribute:
    """Stand-in for ``model.DexpiAttribute`` -- the lookups only need name/value."""

    def __init__(self, name, value):
        self.name = name
        self.value = value


class _Item:
    def __init__(self, *attributes):
        self.attributes = list(attributes)


def test_property_name_strips_the_rdl_role_suffix():
    assert attributes.property_name("TagNameAssignmentClass") == "TagName"
    assert attributes.property_name("PipingClassArtefactSpecialization") == "PipingClassArtefact"
    assert attributes.property_name("NominalDiameterRepresentation") == "NominalDiameterRepresentation"


def test_lookups_accept_both_flavours_of_attribute_name():
    proteus = _Item(_Attribute("TagNameAssignmentClass", "P-100"))
    dexpi20 = _Item(_Attribute("TagName", "P-100"))
    assert attributes.tag_of(proteus) == "P-100"
    assert attributes.tag_of(dexpi20) == "P-100"


def test_tag_falls_back_to_sub_tag():
    item = _Item(_Attribute("SubTagNameAssignmentClass", "N1"))
    assert attributes.tag_of(item) == "N1"
    assert attributes.tag_of(_Item()) is None
    assert attributes.tag_of(_Item(_Attribute("TagNameAssignmentClass", "  "))) is None


def test_medium_spec_and_description():
    item = _Item(
        _Attribute("FluidCodeAssignmentClass", "PW"),
        _Attribute("PipingClassCodeAssignmentClass", "CS150"),
        _Attribute("EquipmentDescriptionAssignmentClass", "feed pump"),
        _Attribute("MaterialOfConstructionCodeAssignmentClass", "316L"),
    )
    assert attributes.medium_of(item) == "PW"
    assert attributes.spec_of(item) == "CS150"
    assert attributes.description_of(item) == "feed pump"
    assert attributes.material_of(item) == "316L"


def test_nominal_diameter_prefers_the_numeric_pair():
    item = _Item(
        _Attribute("NominalDiameterNumericalValueRepresentationAssignmentClass", "100"),
        _Attribute("NominalDiameterTypeRepresentationAssignmentClass", "DN"),
        _Attribute("NominalDiameterRepresentationAssignmentClass", "DN 25"),
    )
    assert attributes.nominal_diameter_of(item) == pytest.approx(0.1)


def test_nominal_diameter_falls_back_to_the_text_form():
    item = _Item(_Attribute("NominalDiameterRepresentationAssignmentClass", "DN 80"))
    assert attributes.nominal_diameter_of(item) == pytest.approx(0.08)
    assert attributes.nominal_diameter_of(_Item()) is None


def test_nominal_diameter_honours_an_nps_type():
    item = _Item(
        _Attribute("NominalDiameterNumericalValueRepresentation", "2"),
        _Attribute("NominalDiameterTypeRepresentation", "NPS"),
    )
    assert attributes.nominal_diameter_of(item) == pytest.approx(0.05)
