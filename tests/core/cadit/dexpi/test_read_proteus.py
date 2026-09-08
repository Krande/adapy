"""The Proteus reader, and above all its normalization of positional node references.

Every fixture here is hand-authored inline. The shared example files land in a later PR, and these
checks are about the traps a real emitter sets -- an anchor at ordinal 1, a ``@NumPoints`` that
disagrees with the node count, a nested chamber, a node reference written the wrong way -- which are
easier to state as five lines of XML than to find in a 400 KB export.
"""

from __future__ import annotations

import contextlib
import logging
import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi import ItemKind, validate_document
from ada.cadit.dexpi.read import read_proteus

# A tank and a pump joined by one segment through a ball valve. Every <ConnectionPoints> opens with
# an anchor at ordinal 1, so every positional reference in it is off by one unless the reader knows.
TINY = """
<PlantModel>
  <PlantInformation Application="Dexpi" Date="2026-01-02" Time="03:04:05" OriginatingSystem="Test Kit"
                    OriginatingSystemVendor="Test Vendor" OriginatingSystemVersion="1.0"
                    SchemaVersion="4.1.1" Units="mm">
    <UnitsOfMeasure/>
  </PlantInformation>
  <Equipment ID="Tank-1" ComponentClass="Tank" ComponentClassURI="http://example.invalid/rdl/Tank">
    <GenericAttributes Set="DexpiAttributes" Number="99">
      <GenericAttribute Name="TagNameAssignmentClass" AttributeURI="http://example.invalid/rdl/TagName"
                        Format="string" Value="T-100"/>
      <GenericAttribute Name="DesignPressure" AttributeURI="http://example.invalid/rdl/DesignPressure"
                        Format="double" Value="16" Units="Bar" UnitsURI="http://example.invalid/rdl/Bar"/>
    </GenericAttributes>
    <Position>
      <Location X="0.1" Y="0.2" Z="0"/>
      <Axis X="0" Y="0" Z="1"/>
      <Reference X="1" Y="0" Z="0"/>
    </Position>
    <Extent>
      <Min X="0.05" Y="0.15"/>
      <Max X="0.15" Y="0.25"/>
    </Extent>
    <Nozzle ID="Nozzle-1" ComponentClass="Nozzle" ComponentClassURI="http://example.invalid/rdl/Nozzle">
      <ConnectionPoints NumPoints="2">
        <Node ID="Nozzle-1-DefaultNode"/>
        <Node ID="PipingNode-1" Type="process">
          <Position><Location X="0.12" Y="0.25" Z="0"/><Reference X="0" Y="1" Z="0"/></Position>
        </Node>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <Equipment ID="Pump-1" ComponentClass="CentrifugalPump" ComponentClassURI="http://example.invalid/rdl/Pump">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="P-100"/>
    </GenericAttributes>
    <Nozzle ID="Nozzle-2" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="2">
        <Node ID="Nozzle-2-DefaultNode"/>
        <Node ID="PipingNode-2" Type="process"/>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <PipingNetworkSystem ID="PNS-1" ComponentClass="PipingNetworkSystem">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="LineNumberAssignmentClass" Format="string" Value="100"/>
      <GenericAttribute Name="FluidCodeAssignmentClass" Format="string" Value="PW"/>
    </GenericAttributes>
    <PipingNetworkSegment ID="PNS-1-S1" ComponentClass="PipingNetworkSegment">
      <PipingComponent ID="Valve-1" ComponentClass="BallValve" ComponentName="BALL_VALVE_SHAPE">
        <ConnectionPoints FlowIn="1" FlowOut="2" NumPoints="3">
          <Node ID="Valve-1-Node-0"/>
          <Node ID="Valve-1-Node-1" Type="process"/>
          <Node ID="Valve-1-Node-2" Type="process"/>
        </ConnectionPoints>
      </PipingComponent>
      <CenterLine NumPoints="0"/>
      <Connection FromID="Nozzle-1" FromNode="1" ToID="Valve-1" ToNode="1"/>
      <Connection FromID="Valve-1" FromNode="2" ToID="Nozzle-2" ToNode="1"/>
    </PipingNetworkSegment>
  </PipingNetworkSystem>
  <ShapeCatalogue Name="symbols">
    <Equipment ID="TANK_SHAPE" ComponentName="TANK_SHAPE"/>
  </ShapeCatalogue>
  <Drawing Name="sheet"/>
</PlantModel>
"""


@contextlib.contextmanager
def captured_warnings():
    """Records logged by adapy's own logger.

    ``configure_logger`` turns propagation off, so ``caplog`` -- which listens on the root -- never
    sees them; this attaches to the ``ada`` logger directly instead.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collector(level=logging.WARNING)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def parse(xml: str):
    return read_proteus(ET.fromstring(xml))


@pytest.fixture
def tiny():
    return parse(TINY)


# -- the document ---------------------------------------------------------------------------------


def test_the_tiny_document_is_structurally_valid(tiny):
    assert validate_document(tiny) == []
    assert tiny.warnings == []


def test_the_header_comes_off_plant_information(tiny):
    assert tiny.header.originating_system == "Test Kit"
    assert tiny.header.originating_system_vendor == "Test Vendor"
    assert tiny.header.originating_system_version == "1.0"
    assert tiny.header.export_date == "2026-01-02"
    assert tiny.header.export_time == "03:04:05"
    assert tiny.header.schema_version == "4.1.1"
    assert tiny.header.units == "mm"


def test_items_are_indexed_flat_with_the_tree_recoverable(tiny):
    assert set(tiny.items) == {"Tank-1", "Nozzle-1", "Pump-1", "Nozzle-2", "PNS-1", "PNS-1-S1", "Valve-1"}
    assert tiny.root_ids == ["Tank-1", "Pump-1", "PNS-1"]
    assert tiny.items["Nozzle-1"].parent_id == "Tank-1"
    assert tiny.items["Tank-1"].child_ids == ["Nozzle-1"]
    assert [item.id for item in tiny.ancestors("Valve-1")] == ["PNS-1-S1", "PNS-1"]


def test_kinds_and_class_uris_survive(tiny):
    assert tiny.items["Tank-1"].kind is ItemKind.EQUIPMENT
    assert tiny.items["Nozzle-1"].kind is ItemKind.NOZZLE
    assert tiny.items["Valve-1"].kind is ItemKind.PIPING_COMPONENT
    assert tiny.items["PNS-1"].kind is ItemKind.PIPING_SYSTEM
    assert tiny.items["PNS-1-S1"].kind is ItemKind.PIPING_SEGMENT
    assert tiny.items["Tank-1"].class_uri == "http://example.invalid/rdl/Tank"
    # The Proteus element tag is kept, because <Equipment ComponentClass="Chamber"> and
    # <Equipment ComponentClass="Tank"> are the same tag and very different things.
    assert tiny.items["Valve-1"].composition_role == "PipingComponent"


def test_the_shape_catalogue_and_drawing_land_in_extras(tiny):
    assert [element.tag for element in tiny.extras] == ["ShapeCatalogue", "Drawing"]
    # ... and their contents never become items, however many IDs they carry.
    assert "TANK_SHAPE" not in tiny.items


def test_every_item_keeps_its_source_element(tiny):
    for item in tiny.items.values():
        assert isinstance(item.raw, ET.Element)
    assert tiny.items["Tank-1"].raw.get("ComponentClass") == "Tank"


# -- nodes ----------------------------------------------------------------------------------------


def test_the_anchor_is_detected_and_still_occupies_ordinal_one(tiny):
    nozzle = tiny.items["Nozzle-1"]
    assert [(node.id, node.ordinal, node.is_anchor) for node in nozzle.nodes] == [
        ("Nozzle-1-DefaultNode", 1, True),
        ("PipingNode-1", 2, False),
    ]
    assert [node.ordinal for node in nozzle.process_nodes] == [2]


def test_an_anchor_is_recognised_by_a_missing_type_as_well_as_by_its_name(tiny):
    # Valve-1-Node-0 is named nothing in particular; it is the anchor because it carries no Type.
    valve = tiny.items["Valve-1"]
    assert [node.is_anchor for node in valve.nodes] == [True, False, False]


def test_num_points_is_ignored_in_favour_of_the_node_count():
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank">
        <Nozzle ID="N1" ComponentClass="Nozzle">
          <ConnectionPoints NumPoints="7">
            <Node ID="N1-DefaultNode"/>
            <Node ID="P1" Type="process"/>
            <Node ID="P2" Type="process"/>
          </ConnectionPoints>
        </Nozzle>
      </Equipment>
    </PlantModel>
    """
    doc = parse(xml)
    assert [node.ordinal for node in doc.items["N1"].nodes] == [1, 2, 3]
    assert validate_document(doc) == []


def test_generic_attributes_number_is_ignored_too(tiny):
    # The tank declares Number="99" over two attributes.
    assert len(tiny.items["Tank-1"].attributes) == 2


def test_a_node_without_an_id_gets_a_synthetic_one():
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank">
        <Nozzle ID="N1" ComponentClass="Nozzle">
          <ConnectionPoints NumPoints="2">
            <Node/>
            <Node Type="process"/>
          </ConnectionPoints>
        </Nozzle>
      </Equipment>
    </PlantModel>
    """
    doc = parse(xml)
    assert [node.id for node in doc.items["N1"].nodes] == ["N1#node1", "N1#node2"]


def test_all_untyped_nodes_leave_only_the_first_as_the_anchor():
    # An emitter that omits Type everywhere says nothing about which node is the anchor, and
    # calling all of them anchors would leave the item with no process connections at all.
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank">
        <Nozzle ID="N1" ComponentClass="Nozzle">
          <ConnectionPoints NumPoints="3"><Node/><Node/><Node/></ConnectionPoints>
        </Nozzle>
      </Equipment>
    </PlantModel>
    """
    doc = parse(xml)
    assert [node.is_anchor for node in doc.items["N1"].nodes] == [True, False, False]


def test_node_geometry_and_diameter_are_parsed(tiny):
    node = tiny.items["Nozzle-1"].nodes[1]
    assert node.position == (0.12, 0.25, 0.0)
    assert node.direction == (0.0, 1.0, 0.0)
    assert node.node_type == "process"


def test_a_nodes_nominal_diameter_comes_off_its_own_attributes():
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank">
        <Nozzle ID="N1" ComponentClass="Nozzle">
          <ConnectionPoints NumPoints="2">
            <Node ID="N1-DefaultNode"/>
            <Node ID="P1" Type="process">
              <GenericAttributes Set="DexpiAttributes" Number="1">
                <GenericAttribute Name="NominalDiameterNumericalValueRepresentationAssignmentClass"
                                  Format="string" Value="80"/>
              </GenericAttributes>
            </Node>
          </ConnectionPoints>
        </Nozzle>
      </Equipment>
    </PlantModel>
    """
    doc = parse(xml)
    assert doc.items["N1"].nodes[1].nominal_diameter == pytest.approx(0.08)


# -- positional resolution -------------------------------------------------------------------------


def test_from_node_resolves_positionally_to_a_node_id(tiny):
    first, second = tiny.connections
    # FromNode="1" is the node at index 1, i.e. ordinal 2 -- never the anchor at ordinal 1.
    assert (first.from_item, first.from_node) == ("Nozzle-1", "PipingNode-1")
    assert (first.to_item, first.to_node) == ("Valve-1", "Valve-1-Node-1")
    assert (second.from_item, second.from_node) == ("Valve-1", "Valve-1-Node-2")
    assert (second.to_item, second.to_node) == ("Nozzle-2", "PipingNode-2")
    assert first.owner_id == "PNS-1-S1"


def test_a_positional_reference_never_lands_on_an_anchor(tiny):
    for connection in tiny.connections:
        for item_id, node_id in (
            (connection.from_item, connection.from_node),
            (connection.to_item, connection.to_node),
        ):
            node = tiny.items[item_id].node_by_id(node_id)
            assert node is not None and not node.is_anchor


def test_flow_in_and_flow_out_are_ordinals_into_the_same_index(tiny):
    valve = tiny.items["Valve-1"]
    assert [node.flow for node in valve.nodes] == [None, "in", "out"]


def test_a_node_id_written_into_a_positional_slot_is_tolerated_with_a_warning():
    xml = TINY.replace('FromID="Nozzle-1" FromNode="1"', 'FromID="Nozzle-1" FromNode="PipingNode-1"')
    with captured_warnings() as records:
        doc = parse(xml)

    assert doc.connections[0].from_node == "PipingNode-1"
    assert any("resolved by node ID in a positional slot" in record.getMessage() for record in records)
    assert any("node ID in a positional slot" in message for message in doc.warnings)


def test_a_one_based_emitter_is_tolerated_with_a_warning():
    # Valve-1 has three nodes, so a 1-based "3" is out of range for the 0-based reading and can
    # only mean the last node. That is the single case where the fallback is unambiguous.
    xml = TINY.replace('FromID="Valve-1" FromNode="2"', 'FromID="Valve-1" FromNode="3"')
    with captured_warnings() as records:
        doc = parse(xml)

    assert doc.connections[1].from_node == "Valve-1-Node-2"
    assert any("resolved by 1-based position" in record.getMessage() for record in records)
    assert any("1-based position" in message for message in doc.warnings)


def test_an_unresolvable_reference_is_collected_not_raised():
    xml = TINY.replace('ToID="Valve-1" ToNode="1"', 'ToID="Valve-1" ToNode="9"')
    with captured_warnings() as records:
        doc = parse(xml)

    assert doc.connections[0].to_item == "Valve-1"
    assert doc.connections[0].to_node is None
    assert any("does not resolve" in record.getMessage() for record in records)
    # ... and the rest of the document is still there.
    assert doc.connections[1].to_node == "PipingNode-2"
    assert len(doc.items) == 7


def test_a_dangling_connection_end_is_legal_and_silent():
    xml = TINY.replace('<Connection FromID="Valve-1" FromNode="2" ToID="Nozzle-2" ToNode="1"/>', "")
    xml = xml.replace(
        '<Connection FromID="Nozzle-1" FromNode="1" ToID="Valve-1" ToNode="1"/>',
        '<Connection FromID="Nozzle-1" FromNode="1"/>',
    )
    doc = parse(xml)
    assert (doc.connections[0].from_node, doc.connections[0].to_item) == ("PipingNode-1", None)
    assert doc.warnings == []


def test_a_connection_to_an_item_without_connection_points_warns():
    xml = TINY.replace('ToID="Valve-1" ToNode="1"', 'ToID="PNS-1" ToNode="1"')
    doc = parse(xml)
    assert any("has no connection points" in message for message in doc.warnings)


# -- nesting ------------------------------------------------------------------------------------


CHAMBERS = """
<PlantModel>
  <Equipment ID="HX-1" ComponentClass="PlateHeatExchanger">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="H-1201"/>
    </GenericAttributes>
    <Equipment ID="Chamber-1" ComponentClass="Chamber">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="Shell"/>
      </GenericAttributes>
      <Association Type="is the location of" ItemID="Nozzle-3"/>
      <Nozzle ID="Nozzle-3" ComponentClass="Nozzle">
        <ConnectionPoints NumPoints="2">
          <Node ID="Nozzle-3-DefaultNode"/>
          <Node ID="PipingNode-3" Type="process"/>
        </ConnectionPoints>
      </Nozzle>
    </Equipment>
  </Equipment>
</PlantModel>
"""


def test_a_nested_chamber_is_recorded_against_its_parent_not_as_a_plant_asset():
    doc = parse(CHAMBERS)
    chamber = doc.items["Chamber-1"]

    assert chamber.kind is ItemKind.CHAMBER
    assert chamber.parent_id == "HX-1"
    assert chamber.composition_role == "Equipment"
    assert chamber.tag == "Shell"
    assert doc.root_ids == ["HX-1"]
    # The equipment classification is by supertype; Chamber does not derive from ProcessEquipment.
    assert doc.items["HX-1"].kind is ItemKind.EQUIPMENT
    assert [item.id for item in doc.by_kind(ItemKind.EQUIPMENT)] == ["HX-1"]


def test_a_nozzle_three_levels_down_is_still_addressable_flat():
    doc = parse(CHAMBERS)
    assert doc.items["Nozzle-3"].parent_id == "Chamber-1"
    assert doc.find_node("PipingNode-3")[0].id == "Nozzle-3"
    assert validate_document(doc) == []


# -- attributes, associations, geometry -------------------------------------------------------------


def test_generic_attributes_keep_every_field_and_their_set(tiny):
    tag, pressure = tiny.items["Tank-1"].attributes

    assert (tag.name, tag.value, tag.format, tag.set_name) == (
        "TagNameAssignmentClass",
        "T-100",
        "string",
        "DexpiAttributes",
    )
    assert tag.uri == "http://example.invalid/rdl/TagName"
    assert (pressure.units, pressure.units_uri) == ("Bar", "http://example.invalid/rdl/Bar")
    assert tiny.items["Tank-1"].tag == "T-100"


def test_a_value_uri_and_a_language_are_kept():
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="PrimarySecondaryPipingNetworkSegmentSpecialization" Format="anyURI"
                            Value="PrimaryPipingNetworkSegment" ValueURI="http://example.invalid/rdl/Primary"/>
          <GenericAttribute Name="EquipmentDescriptionAssignmentClass" Format="string" Value="Feed tank"
                            Language="en-GB"/>
        </GenericAttributes>
      </Equipment>
    </PlantModel>
    """
    specialization, description = parse(xml).items["E1"].attributes
    assert specialization.value_uri == "http://example.invalid/rdl/Primary"
    assert description.language == "en-GB"


def test_a_vendor_attribute_set_is_kept_alongside_the_dexpi_one():
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="T-100"/>
        </GenericAttributes>
        <GenericAttributes Set="VendorProperties">
          <GenericAttribute Name="SystemUID" Format="string" Value="abc123"/>
        </GenericAttributes>
      </Equipment>
    </PlantModel>
    """
    attributes = parse(xml).items["E1"].attributes
    assert [(a.set_name, a.name) for a in attributes] == [
        ("DexpiAttributes", "TagNameAssignmentClass"),
        ("VendorProperties", "SystemUID"),
    ]


def test_associations_are_parsed_one_per_element():
    doc = parse(CHAMBERS)
    association = doc.items["Chamber-1"].associations[0]
    assert (association.type, association.owner_id, association.target_ids) == (
        "is the location of",
        "Chamber-1",
        ["Nozzle-3"],
    )


def test_position_and_extent_are_parsed_from_the_items_own_children(tiny):
    placement = tiny.items["Tank-1"].placement
    assert placement.location == (0.1, 0.2, 0.0)
    assert placement.axis == (0.0, 0.0, 1.0)
    assert placement.reference == (1.0, 0.0, 0.0)
    assert placement.extent_min == (0.05, 0.15, 0.0)
    assert placement.extent_max == (0.15, 0.25, 0.0)
    # The nozzle's own <Position> belongs to the nozzle, not to the tank.
    assert tiny.items["Nozzle-1"].placement is None


def test_a_polyline_is_collected_as_points():
    xml = """
    <PlantModel>
      <PipingNetworkSystem ID="PNS-1" ComponentClass="PipingNetworkSystem">
        <PipingNetworkSegment ID="S1" ComponentClass="PipingNetworkSegment">
          <PolyLine NumPoints="3">
            <Coordinate X="0" Y="0"/>
            <Coordinate X="1" Y="0"/>
            <Coordinate X="1" Y="2"/>
          </PolyLine>
        </PipingNetworkSegment>
      </PipingNetworkSystem>
    </PlantModel>
    """
    assert parse(xml).items["S1"].placement.polyline == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 2.0, 0.0)]


def test_persistent_id_and_component_name_are_kept_as_metadata():
    xml = """
    <PlantModel>
      <Equipment ID="E1" ComponentClass="Tank" ComponentName="TANK_SHAPE" TagName="T-100">
        <PersistentID Identifier="ABC-123" Context="Some Tool"/>
      </Equipment>
    </PlantModel>
    """
    metadata = parse(xml).items["E1"].metadata
    assert metadata["persistent_id"] == {"identifier": "ABC-123", "context": "Some Tool"}
    assert metadata["component_name"] == "TANK_SHAPE"
    assert metadata["proteus_attributes"] == {"TagName": "T-100"}


# -- sources and failure modes ---------------------------------------------------------------------


def test_reading_from_a_file_records_the_source(tmp_path):
    path = tmp_path / "tiny_proteus.xml"
    path.write_text(TINY, encoding="utf-8")

    doc = read_proteus(path)
    assert doc.source == str(path)
    assert len(doc.items) == 7


def test_reading_a_tree_works_too(tmp_path):
    path = tmp_path / "tiny_proteus.xml"
    path.write_text(TINY, encoding="utf-8")
    assert len(read_proteus(ET.parse(path)).items) == 7


def test_a_non_proteus_root_is_refused():
    with pytest.raises(ValueError, match="root element is <Model>"):
        parse("<Model/>")


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_proteus(tmp_path / "absent.xml")
