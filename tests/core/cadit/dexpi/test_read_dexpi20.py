"""The DEXPI 2.0 reader, and the constructs its generic element vocabulary hides plant meaning in.

Every fixture here is hand-authored inline. The shared example files land in a later PR, and these
checks are about the eleven element names ``DEXPI_XML_Schema.xsd`` defines -- ``<Components>``,
``<Data>``, ``<References>``, ``<ObjectReference>``, ``<DataReference>``, ``<AggregatedDataValue>``
and the five literals -- which are easier to state as ten lines of XML than to find in the 1.5 MB
reference P&ID.
"""

from __future__ import annotations

import socket
import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi import DexpiFlavour, ItemKind, validate_document
from ada.cadit.dexpi.read import read_dexpi20

# A tank and a pump joined by one segment through a ball valve -- the same P&ID the Proteus tests
# use, written the DEXPI 2.0 way: one <Object type> per concept, connectivity by ID.
TINY = """
<Model name="tiny" uri="http://example.invalid/tiny">
  <Import prefix="Core" source="https://data.dexpi.org/models/2.0.0/Core.xml"/>
  <Import prefix="Plant" source="https://data.dexpi.org/models/2.0.0/Plant.xml"/>
  <Object type="Core/EngineeringModel">
    <Data property="OriginatingSystemName"><String>Test Kit</String></Data>
    <Data property="OriginatingSystemVendorName"><String>Test Vendor</String></Data>
    <Data property="OriginatingSystemVersion"><String>1.0</String></Data>
    <Components property="ConceptualModel">
      <Object id="PlantModel1" type="Plant/PlantModel">
        <Components property="TaggedPlantItems">
          <Object id="Tank1" type="Plant/ProcessEquipment.Tank">
            <Data property="TagName"><String>T-100</String></Data>
            <Data property="UpperLimitDesignPressure">
              <AggregatedDataValue type="Core/PhysicalQuantities.PhysicalQuantity">
                <Data property="Unit">
                  <DataReference data="Core/PhysicalQuantities.PressureGaugeUnit.Bar"/>
                </Data>
                <Data property="Value"><Double>16.0</Double></Data>
              </AggregatedDataValue>
            </Data>
            <Components property="Nozzles">
              <Object id="Nozzle1" type="Plant/ProcessEquipment.Nozzle">
                <Data property="SubTagName"><String>N1</String></Data>
                <Components property="Nodes">
                  <Object id="PipingNode1" type="Plant/Piping.PipingNode">
                    <Data property="NominalDiameterNumericalValueRepresentation"><String>80</String></Data>
                    <Data property="NominalDiameterTypeRepresentation"><String>DN</String></Data>
                  </Object>
                </Components>
              </Object>
            </Components>
          </Object>
          <Object id="Pump1" type="Plant/ProcessEquipment.CentrifugalPump">
            <Data property="TagName"><String>P-100</String></Data>
            <Components property="Nozzles">
              <Object id="Nozzle2" type="Plant/ProcessEquipment.Nozzle">
                <Data property="SubTagName"><String>S</String></Data>
                <Components property="Nodes">
                  <Object id="PipingNode2" type="Plant/Piping.PipingNode"/>
                </Components>
              </Object>
            </Components>
          </Object>
        </Components>
        <Components property="PipingNetworkSystems">
          <Object id="PipingNetworkSystem1" type="Plant/Piping.PipingNetworkSystem">
            <Data property="LineNumber"><String>100</String></Data>
            <Data property="FluidCode"><String>PW</String></Data>
            <Components property="Segments">
              <Object id="Segment1" type="Plant/Piping.PipingNetworkSegment">
                <Data property="SegmentNumber"><String>S1</String></Data>
                <Data property="PrimarySecondaryPipingNetworkSegment">
                  <DataReference data="Plant/Enumerations.PrimarySecondary.PrimaryPipingNetworkSegment"/>
                </Data>
                <Components property="Items">
                  <Object id="Valve1" type="Plant/Piping.BallValve">
                    <Data property="TagName"><String>HV-100</String></Data>
                    <Components property="Nodes">
                      <Object id="PipingNode3" type="Plant/Piping.PipingNode"/>
                      <Object id="PipingNode4" type="Plant/Piping.PipingNode"/>
                    </Components>
                  </Object>
                </Components>
                <Components property="Connections">
                  <Object id="Pipe1" type="Plant/Piping.Pipe">
                    <References property="SourceItem" objects="#Nozzle1"/>
                    <References property="SourceNode" objects="#PipingNode1"/>
                    <References property="TargetItem" objects="#Valve1"/>
                    <References property="TargetNode" objects="#PipingNode3"/>
                  </Object>
                  <Object id="Pipe2" type="Plant/Piping.Pipe">
                    <References property="SourceItem" objects="#Valve1"/>
                    <References property="SourceNode" objects="#PipingNode4"/>
                    <References property="TargetItem" objects="#Nozzle2"/>
                    <References property="TargetNode" objects="#PipingNode2"/>
                  </Object>
                </Components>
                <References property="SourceItem" objects="#Nozzle1"/>
                <References property="SourceNode" objects="#PipingNode1"/>
                <References property="TargetItem" objects="#Nozzle2"/>
                <References property="TargetNode" objects="#PipingNode2"/>
              </Object>
            </Components>
          </Object>
        </Components>
      </Object>
    </Components>
    <Components property="Diagram">
      <Object id="Diagram1" type="Core/Diagram.Diagram">
        <Components property="Elements">
          <Object id="Label1" type="Plant/Diagram.EquipmentTagNameLabel"/>
        </Components>
      </Object>
    </Components>
  </Object>
</Model>
"""


def parse(xml: str):
    return read_dexpi20(ET.fromstring(xml))


@pytest.fixture
def tiny():
    return parse(TINY)


# -- the document ----------------------------------------------------------------------------------


def test_the_tiny_document_is_structurally_valid(tiny):
    assert tiny.flavour is DexpiFlavour.DEXPI20
    assert validate_document(tiny) == []
    assert tiny.warnings == []


def test_imports_are_recorded_on_the_header(tiny):
    assert tiny.header.imports == {
        "Core": "https://data.dexpi.org/models/2.0.0/Core.xml",
        "Plant": "https://data.dexpi.org/models/2.0.0/Plant.xml",
    }
    # The only place a DEXPI 2.0 file states which specification it was written against.
    assert tiny.header.schema_version == "2.0.0"


def test_an_import_is_never_fetched():
    """The prefixes are static names answered by the vendored class table.

    Reaching for the network to parse a file would be wrong even if the certificate on
    ``data.dexpi.org`` were valid, which it is not -- so this parses with the socket layer
    sabotaged, and asserts the classes still resolve.
    """

    def refuse(*args, **kwargs):
        raise AssertionError("the reader opened a socket while parsing a DEXPI 2.0 document")

    original = socket.socket
    socket.socket = refuse
    try:
        doc = parse(TINY)
    finally:
        socket.socket = original

    assert doc.items["Tank1"].kind is ItemKind.EQUIPMENT
    assert doc.items["Tank1"].class_uri == "https://data.dexpi.org/models/2.0.0/Plant.xml#Plant/ProcessEquipment.Tank"


def test_the_header_comes_off_the_model_and_its_envelope(tiny):
    assert tiny.header.project == "tiny"
    assert tiny.header.model_uri == "http://example.invalid/tiny"
    assert tiny.header.originating_system == "Test Kit"
    assert tiny.header.originating_system_vendor == "Test Vendor"
    assert tiny.header.originating_system_version == "1.0"


def test_the_model_envelope_is_the_document_not_an_item(tiny):
    # <Model>/EngineeringModel/PlantModel is what Proteus writes as the <PlantModel> root element
    # and its <PlantInformation> header: the document, not an item in it. Keeping it as two items
    # would put EngineeringModel[]/PlantModel[] in front of every path in the graph signature.
    assert "PlantModel1" not in tiny.items
    assert [item.class_name for item in tiny.items.values() if item.kind is ItemKind.MODEL] == []
    assert tiny.root_ids == ["Tank1", "Pump1", "PipingNetworkSystem1"]


def test_items_are_indexed_flat_with_the_tree_recoverable(tiny):
    assert set(tiny.items) == {"Tank1", "Nozzle1", "Pump1", "Nozzle2", "PipingNetworkSystem1", "Segment1", "Valve1"}
    assert tiny.items["Nozzle1"].parent_id == "Tank1"
    assert tiny.items["Tank1"].child_ids == ["Nozzle1"]
    assert [item.id for item in tiny.ancestors("Valve1")] == ["Segment1", "PipingNetworkSystem1"]


def test_kinds_are_classified_from_the_qualified_type(tiny):
    assert tiny.items["Tank1"].kind is ItemKind.EQUIPMENT
    assert tiny.items["Nozzle1"].kind is ItemKind.NOZZLE
    assert tiny.items["Valve1"].kind is ItemKind.PIPING_COMPONENT
    assert tiny.items["PipingNetworkSystem1"].kind is ItemKind.PIPING_SYSTEM
    assert tiny.items["Segment1"].kind is ItemKind.PIPING_SEGMENT


def test_nested_composition_roles_record_the_property_the_object_hangs_off(tiny):
    assert tiny.items["Tank1"].composition_role == "TaggedPlantItems"
    assert tiny.items["Nozzle1"].composition_role == "Nozzles"
    assert tiny.items["PipingNetworkSystem1"].composition_role == "PipingNetworkSystems"
    assert tiny.items["Segment1"].composition_role == "Segments"
    assert tiny.items["Valve1"].composition_role == "Items"


def test_the_qualified_type_survives_on_metadata(tiny):
    assert tiny.items["Valve1"].metadata["dexpi_type"] == "Plant/Piping.BallValve"
    assert tiny.items["Valve1"].class_name == "BallValve"


def test_the_diagram_lands_in_extras_and_never_becomes_items(tiny):
    assert [element.get("type") for element in tiny.extras] == ["Core/Diagram.Diagram"]
    assert "Diagram1" not in tiny.items and "Label1" not in tiny.items


def test_every_item_keeps_its_source_element(tiny):
    for item in tiny.items.values():
        assert isinstance(item.raw, ET.Element)
    assert tiny.items["Tank1"].raw.get("type") == "Plant/ProcessEquipment.Tank"


# -- Data ------------------------------------------------------------------------------------------


def test_a_literal_data_value_becomes_an_attribute_with_its_type_as_the_format(tiny):
    tag, pressure = tiny.items["Tank1"].attributes
    assert (tag.name, tag.value, tag.format) == ("TagName", "T-100", "string")
    # The DEXPI 2.0 property name, not the Proteus assignment class -- and the tag lookup takes
    # either, so a consumer never has to know which flavour it is holding.
    assert tiny.items["Tank1"].tag == "T-100"
    assert pressure.name == "UpperLimitDesignPressure"


@pytest.mark.parametrize(
    "literal, expected_value, expected_format",
    [
        ("<String>abc</String>", "abc", "string"),
        ("<Double>6.5</Double>", "6.5", "double"),
        ("<Integer>42</Integer>", "42", "integer"),
        ("<Boolean>false</Boolean>", "false", "boolean"),
        ("<DateTime>2016-04-01T00:00:00</DateTime>", "2016-04-01T00:00:00", "dateTime"),
        ("<Undefined/>", None, "undefined"),
    ],
)
def test_every_literal_data_type_is_read(literal, expected_value, expected_format):
    xml = f"""
    <Model name="m" uri="http://example.invalid/m">
      <Object id="T1" type="Plant/ProcessEquipment.Tank">
        <Data property="Whatever">{literal}</Data>
      </Object>
    </Model>
    """
    attribute = parse(xml).items["T1"].attributes[0]
    assert (attribute.value, attribute.format) == (expected_value, expected_format)


def test_a_data_reference_keeps_the_enumeration_literal_and_the_whole_reference(tiny):
    segment = tiny.items["Segment1"]
    number, classification = segment.attributes

    assert (number.name, number.value) == ("SegmentNumber", "S1")
    # Proteus writes an enumerated value as Value plus ValueURI; a DataReference is the same thing
    # said once, so it is split the same way.
    assert classification.value == "PrimaryPipingNetworkSegment"
    assert classification.value_uri == "Plant/Enumerations.PrimarySecondary.PrimaryPipingNetworkSegment"
    assert classification.format == "anyURI"


def test_a_physical_quantity_carries_its_value_and_its_unit(tiny):
    pressure = tiny.items["Tank1"].attributes[1]

    assert (pressure.value, pressure.format) == ("16.0", "double")
    assert pressure.units == "Bar"
    assert pressure.units_uri == "Core/PhysicalQuantities.PressureGaugeUnit.Bar"

    from ada.cadit.dexpi import units

    assert units.to_si(float(pressure.value), pressure.units) == pytest.approx(16e5)


def test_a_single_language_string_becomes_one_attribute_carrying_its_language():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="T1" type="Plant/ProcessEquipment.Tank">
        <Data property="EquipmentDescription">
          <AggregatedDataValue type="Core/DataTypes.SingleLanguageString">
            <Data property="Language"><String>en</String></Data>
            <Data property="Value"><String>Feed tank</String></Data>
          </AggregatedDataValue>
        </Data>
      </Object>
    </Model>
    """
    attribute = parse(xml).items["T1"].attributes[0]
    assert (attribute.name, attribute.value, attribute.language) == ("EquipmentDescription", "Feed tank", "en")


def test_a_multi_language_string_becomes_one_attribute_per_language():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="T1" type="Plant/ProcessEquipment.Tank">
        <Data property="EquipmentDescription">
          <AggregatedDataValue type="Core/DataTypes.MultiLanguageString">
            <Data property="SingleLanguageStrings">
              <AggregatedDataValue type="Core/DataTypes.SingleLanguageString">
                <Data property="Language"><String>en</String></Data>
                <Data property="Value"><String>approved</String></Data>
              </AggregatedDataValue>
              <AggregatedDataValue type="Core/DataTypes.SingleLanguageString">
                <Data property="Language"><String>de</String></Data>
                <Data property="Value"><String>genehmigt</String></Data>
              </AggregatedDataValue>
            </Data>
          </AggregatedDataValue>
        </Data>
      </Object>
    </Model>
    """
    # Which is exactly how Proteus writes it: one <GenericAttribute> per Language.
    attributes = parse(xml).items["T1"].attributes
    assert [(a.name, a.value, a.language) for a in attributes] == [
        ("EquipmentDescription", "approved", "en"),
        ("EquipmentDescription", "genehmigt", "de"),
    ]


def test_an_unrecognised_aggregate_is_flattened_rather_than_dropped():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="T1" type="Plant/ProcessEquipment.Tank">
        <Data property="Origin">
          <AggregatedDataValue type="Core/Diagram.Point">
            <Data property="X"><Double>1.0</Double></Data>
            <Data property="Y"><Double>296.0</Double></Data>
          </AggregatedDataValue>
        </Data>
      </Object>
    </Model>
    """
    doc = parse(xml)
    assert [(a.name, a.value) for a in doc.items["T1"].attributes] == [("Origin.X", "1.0"), ("Origin.Y", "296.0")]
    assert doc.warnings == []


def test_an_unknown_data_element_is_collected_not_raised():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="T1" type="Plant/ProcessEquipment.Tank">
        <Data property="TagName"><Vendorism>?</Vendorism></Data>
        <Data property="SubTagName"><String>ok</String></Data>
      </Object>
    </Model>
    """
    doc = parse(xml)
    assert any("<Vendorism> is not a DEXPI 2.0 data value" in message for message in doc.warnings)
    assert doc.items["T1"].tag == "ok"


# -- Components ---------------------------------------------------------------------------------------


def test_piping_nodes_become_nodes_on_their_owner_not_items(tiny):
    nozzle = tiny.items["Nozzle1"]
    assert "PipingNode1" not in tiny.items
    assert [(node.id, node.ordinal, node.is_anchor) for node in nozzle.nodes] == [("PipingNode1", 1, False)]
    # DEXPI 2.0 has no symbol anchor at all -- the placement lives in the diagram -- so every node
    # here is a process node and ordinal 1 is a real connection point.
    assert nozzle.process_nodes == nozzle.nodes


def test_a_nodes_nominal_diameter_comes_off_its_own_data(tiny):
    assert tiny.items["Nozzle1"].nodes[0].nominal_diameter == pytest.approx(0.08)
    assert tiny.items["Valve1"].nodes[0].nominal_diameter is None


def test_nodes_are_numbered_in_document_order(tiny):
    assert [node.ordinal for node in tiny.items["Valve1"].nodes] == [1, 2]


def test_an_anonymous_object_is_given_a_deterministic_id():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="AS1" type="Plant/Instrumentation.ActuatingSystem">
        <Components property="OperatedValveReference">
          <Object type="Plant/Instrumentation.OperatedValveReference">
            <Data property="SubTagName"><String>PV-01_YV</String></Data>
          </Object>
          <Object type="Plant/Instrumentation.OperatedValveReference">
            <Data property="SubTagName"><String>PV-02_YV</String></Data>
          </Object>
        </Components>
      </Object>
    </Model>
    """
    # 1314 of the 1612 objects in the specification's own reference P&ID carry no id; anything
    # nothing points at simply goes without one.
    doc = parse(xml)
    assert doc.items["AS1"].child_ids == ["OperatedValveReference#auto1", "OperatedValveReference#auto2"]
    assert doc.items["OperatedValveReference#auto1"].metadata["synthetic_id"] is True
    assert validate_document(doc) == []


def test_reading_the_same_document_twice_mints_the_same_ids():
    assert set(parse(TINY).items) == set(parse(TINY).items)


def test_a_duplicate_id_is_collected_not_raised():
    xml = TINY.replace('<Object id="Nozzle2"', '<Object id="Nozzle1"')
    doc = parse(xml)
    assert any("duplicate object id 'Nozzle1'" in message for message in doc.warnings)
    assert doc.items["Nozzle1"].parent_id == "Tank1"


# -- References ------------------------------------------------------------------------------------------


def test_a_pipe_becomes_a_connection_rather_than_an_item(tiny):
    # Proteus writes the same edge as <Connection FromID FromNode ToID ToNode/>, which is not an
    # item either. The DexpiConnection that comes out is indistinguishable from the Proteus one.
    assert "Pipe1" not in tiny.items and "Pipe2" not in tiny.items

    first, second = tiny.connections
    assert (first.from_item, first.from_node) == ("Nozzle1", "PipingNode1")
    assert (first.to_item, first.to_node) == ("Valve1", "PipingNode3")
    assert (second.from_item, second.from_node) == ("Valve1", "PipingNode4")
    assert (second.to_item, second.to_node) == ("Nozzle2", "PipingNode2")
    assert first.owner_id == "Segment1"


def test_the_segments_own_end_points_stay_associations(tiny):
    # A PipingNetworkSegment repeats the ends of its first and last pipe. Emitting those as
    # connections too would double every edge in the graph, and Proteus has no element for them.
    assert len(tiny.connections) == 2
    assert [(a.type, a.target_ids) for a in tiny.items["Segment1"].associations] == [
        ("SourceItem", ["Nozzle1"]),
        ("SourceNode", ["PipingNode1"]),
        ("TargetItem", ["Nozzle2"]),
        ("TargetNode", ["PipingNode2"]),
    ]


def test_a_leading_hash_is_stripped_from_every_reference(tiny):
    for connection in tiny.connections:
        for token in (connection.from_item, connection.from_node, connection.to_item, connection.to_node):
            assert token is not None and not token.startswith("#")


def test_a_dangling_connection_end_is_legal_and_silent():
    xml = TINY.replace('<References property="TargetItem" objects="#Valve1"/>', "", 1)
    xml = xml.replace('<References property="TargetNode" objects="#PipingNode3"/>', "", 1)

    doc = parse(xml)
    assert (doc.connections[0].from_item, doc.connections[0].to_item) == ("Nozzle1", None)
    assert doc.warnings == []


def test_a_reference_property_naming_several_objects_warns_and_takes_the_first():
    xml = TINY.replace(
        '<References property="SourceItem" objects="#Nozzle1"/>\n                    ',
        '<References property="SourceItem" objects="#Nozzle1 #Nozzle2"/>\n                    ',
        1,
    )
    doc = parse(xml)
    assert doc.connections[0].from_item == "Nozzle1"
    assert any("names 2 objects" in message for message in doc.warnings)


def test_a_signal_line_is_both_an_item_and_an_edge():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="PIF1" type="Plant/Instrumentation.ProcessInstrumentationFunction">
        <Data property="ProcessInstrumentationFunctionNumber"><String>LIC-201</String></Data>
        <Components property="SignalConveyingFunctions">
          <Object id="SCF1" type="Plant/Instrumentation.MeasuringLineFunction">
            <References property="Source" objects="#PSGF1"/>
            <References property="Target" objects="#PIF1"/>
          </Object>
        </Components>
        <Components property="ProcessSignalGeneratingFunctions">
          <Object id="PSGF1" type="Plant/Instrumentation.ProcessSignalGeneratingFunction"/>
        </Components>
      </Object>
    </Model>
    """
    # Proteus writes this as an <InformationFlow ID=...> holding a <Connection>: an item that is
    # also an edge, with no node at either end.
    doc = parse(xml)
    assert doc.items["SCF1"].kind is ItemKind.INSTRUMENTATION
    assert [(c.from_item, c.from_node, c.to_item, c.owner_id) for c in doc.connections] == [
        ("PSGF1", None, "PIF1", "SCF1")
    ]


# -- ObjectReference ----------------------------------------------------------------------------------


def test_an_object_reference_in_a_components_block_composes_the_target():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="HX1" type="Plant/ProcessEquipment.PlateHeatExchanger">
        <Data property="TagName"><String>E-201</String></Data>
        <Components property="Chambers">
          <ObjectReference object="#Chamber1"/>
        </Components>
      </Object>
      <Object id="Chamber1" type="Plant/ProcessEquipment.Chamber">
        <Data property="SubTagName"><String>Shell</String></Data>
      </Object>
    </Model>
    """
    # The reference points forward, so it can only be resolved once the whole document is read.
    doc = parse(xml)
    assert doc.items["Chamber1"].parent_id == "HX1"
    assert doc.items["Chamber1"].composition_role == "Chambers"
    assert doc.items["HX1"].child_ids == ["Chamber1"]
    assert doc.root_ids == ["HX1"]
    assert validate_document(doc) == []


def test_an_object_reference_to_something_already_composed_becomes_an_association():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="HX1" type="Plant/ProcessEquipment.PlateHeatExchanger">
        <Components property="Chambers">
          <Object id="Chamber1" type="Plant/ProcessEquipment.Chamber"/>
        </Components>
      </Object>
      <Object id="HX2" type="Plant/ProcessEquipment.PlateHeatExchanger">
        <Components property="Chambers">
          <ObjectReference object="#Chamber1"/>
        </Components>
      </Object>
    </Model>
    """
    # Composition is exclusive, so the second claim is recorded but not acted on.
    doc = parse(xml)
    assert doc.items["Chamber1"].parent_id == "HX1"
    assert [(a.type, a.target_ids) for a in doc.items["HX2"].associations] == [("Chambers", ["Chamber1"])]
    assert any("already composed into 'HX1'" in message for message in doc.warnings)
    assert validate_document(doc) == []


def test_an_object_reference_to_nothing_is_collected_not_raised():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="HX1" type="Plant/ProcessEquipment.PlateHeatExchanger">
        <Components property="Chambers"><ObjectReference object="#Absent"/></Components>
      </Object>
    </Model>
    """
    doc = parse(xml)
    assert any("names an object this document does not hold" in message for message in doc.warnings)
    assert set(doc.items) == {"HX1"}


# -- sources and failure modes -----------------------------------------------------------------------


def test_reading_from_a_file_records_the_source(tmp_path):
    path = tmp_path / "tiny_dexpi20.xml"
    path.write_text(TINY, encoding="utf-8")

    doc = read_dexpi20(path)
    assert doc.source == str(path)
    assert len(doc.items) == 7


def test_reading_a_tree_works_too(tmp_path):
    path = tmp_path / "tiny_dexpi20.xml"
    path.write_text(TINY, encoding="utf-8")
    assert len(read_dexpi20(ET.parse(path)).items) == 7


def test_an_object_without_a_type_is_collected_not_raised():
    xml = """
    <Model name="m" uri="http://example.invalid/m">
      <Object id="Mystery"/>
      <Object id="T1" type="Plant/ProcessEquipment.Tank"/>
    </Model>
    """
    doc = parse(xml)
    assert any("carries no type" in message for message in doc.warnings)
    assert set(doc.items) == {"T1"}


def test_a_non_dexpi20_root_is_refused():
    with pytest.raises(ValueError, match="root element is <PlantModel>"):
        parse("<PlantModel/>")


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_dexpi20(tmp_path / "absent.xml")
