"""The DEXPI 2.0 writer.

Same T1 claim as its Proteus twin -- ``file -> doc -> XML -> doc'`` compares equal under
:func:`~ada.cadit.dexpi.canonical.canonicalize` -- over the two generated DEXPI 2.0 fixtures. The
rest of this file pins the four things PR 4 established about the format, each of which a writer can
get wrong in a way that still parses:

* ``Object/@id`` is optional, most objects have none, and a minted ``#auto`` id must not be
  published as though the source had authored it;
* ``References/@objects`` is a space-separated list;
* a ``PipingConnection`` is an edge and never an item, and the owning segment's repeated
  ``SourceItem``/``TargetItem`` references must not be emitted as edges as well;
* there is no symbol anchor in DEXPI 2.0.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi import canonicalize, graph_signature, read_dexpi, validate_document
from ada.cadit.dexpi.read import read_dexpi20
from ada.cadit.dexpi.write import to_element, write_dexpi, write_dexpi20
from ada.cadit.dexpi.write.xml_utils import to_text

DEXPI20_FILES = ("tiny_two_equipment_dexpi20.xml", "unit_separator_dexpi20.xml")


@pytest.fixture
def dexpi_files(example_files):
    return example_files / "dexpi_files"


# -- T1: the lossless echo ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEXPI20_FILES)
def test_t1_a_dexpi20_file_survives_a_write_and_a_re_read(name, dexpi_files, tmp_path):
    doc = read_dexpi(dexpi_files / name)
    again = read_dexpi(write_dexpi(doc, tmp_path / name))

    assert again.warnings == []
    assert validate_document(again) == []
    assert canonicalize(again) == canonicalize(doc)


@pytest.mark.parametrize("name", DEXPI20_FILES)
def test_the_written_file_is_reproducible(name, dexpi_files):
    doc = read_dexpi(dexpi_files / name)
    assert to_text(write_dexpi20(doc)) == to_text(write_dexpi20(doc))


def test_the_round_trip_is_not_vacuous(dexpi_files, tmp_path):
    doc = read_dexpi(dexpi_files / "tiny_two_equipment_dexpi20.xml")
    doc.connections[0].to_node = "BallValve-1-Node-2"

    again = read_dexpi(write_dexpi(doc, tmp_path / "edited.xml"))
    assert again.connections[0].to_node == "BallValve-1-Node-2"
    assert canonicalize(again) != canonicalize(read_dexpi(dexpi_files / "tiny_two_equipment_dexpi20.xml"))


# -- the model envelope ----------------------------------------------------------------------------------


def test_the_envelope_is_written_back_even_though_it_is_not_an_item(dexpi_files):
    """The reader walks through ``Core/EngineeringModel`` and ``Plant/PlantModel`` without making
    items of them, so the writer has to put them back or the document has no root to hang off."""
    root = write_dexpi20(read_dexpi(dexpi_files / "tiny_two_equipment_dexpi20.xml"))

    engineering = root.find("Object")
    assert engineering.get("type") == "Core/EngineeringModel"
    assert engineering.find("Components").get("property") == "ConceptualModel"
    assert engineering.find("Components/Object").get("type") == "Plant/PlantModel"

    assert [element.get("prefix") for element in root.findall("Import")] == ["Core", "Plant"]


def test_the_originating_system_is_written_as_data_on_the_envelope(dexpi_files):
    """DEXPI 2.0 has no ``<PlantInformation>``; provenance is ordinary attribute data."""
    doc = read_dexpi(dexpi_files / "tiny_two_equipment_dexpi20.xml")
    assert doc.header.originating_system == "adapy"
    assert doc.header.originating_system_vendor == "adapy"
    assert doc.header.schema_version == "2.0.0"

    again = read_dexpi20(write_dexpi20(doc))
    assert again.header.originating_system == "adapy"
    assert again.header.project == doc.header.project


# -- ids, references and edges -----------------------------------------------------------------------------


def test_a_minted_id_is_not_published_as_though_it_had_been_authored():
    """``id`` is optional and 1314 of the reference P&ID's 1612 objects have none.

    The reader mints ``{Class}#auto{n}`` for those and flags them; the writer omits the id again, so
    the next reader mints the same one instead of inheriting a fiction.
    """
    doc = read_dexpi20(
        ET.fromstring(
            """
            <Model>
              <Import prefix="Plant" source="https://data.dexpi.org/models/2.0.0/Plant.xml"/>
              <Object type="Core/EngineeringModel">
                <Components property="ConceptualModel">
                  <Object type="Plant/PlantModel">
                    <Components property="TaggedPlantItems">
                      <Object type="Plant/ProcessEquipment.Tank">
                        <Data property="TagName"><String>T-100</String></Data>
                      </Object>
                    </Components>
                  </Object>
                </Components>
              </Object>
            </Model>
            """
        )
    )
    assert list(doc.items) == ["Tank#auto1"]
    assert doc.items["Tank#auto1"].metadata["synthetic_id"] is True

    text = to_text(write_dexpi20(doc))
    assert "auto1" not in text

    again = read_dexpi20(ET.fromstring(text))
    assert canonicalize(again) == canonicalize(doc)


def test_references_are_written_as_a_space_separated_list():
    """``@objects`` is a list in the schema, even though every emitter seen so far writes one."""
    doc = read_dexpi20(
        ET.fromstring(
            """
            <Model>
              <Object type="Core/EngineeringModel">
                <Components property="ConceptualModel">
                  <Object type="Plant/PlantModel">
                    <Components property="TaggedPlantItems">
                      <Object id="Tank1" type="Plant/ProcessEquipment.Tank"/>
                      <Object id="Section1" type="Plant/PlantStructure.PlantSection">
                        <References property="Contains" objects="#Tank1 #Tank1"/>
                      </Object>
                    </Components>
                  </Object>
                </Components>
              </Object>
            </Model>
            """
        )
    )
    assert doc.items["Section1"].associations[0].target_ids == ["Tank1", "Tank1"]

    reference = write_dexpi20(doc).find(".//Object[@id='Section1']/References")
    assert reference.get("objects") == "#Tank1 #Tank1"


def test_a_piping_connection_is_written_as_an_edge_and_never_as_an_item(dexpi_files):
    """And the segment's own repeated endpoint references are not written as edges as well.

    Emitting both is the mistake that doubles every connection in the document; PR 4 found it in
    the reference P&ID, where a ``Pipe`` and its owning ``PipingNetworkSegment`` name the same two
    ends.
    """
    doc = read_dexpi(dexpi_files / "unit_separator_dexpi20.xml")
    root = write_dexpi20(doc)

    pipes = root.findall(".//Components[@property='Connections']/Object")
    assert len(pipes) == len(doc.connections)
    assert {pipe.get("type") for pipe in pipes} == {"Plant/Piping.Pipe"}

    # None of the edges became items on the way back in.
    again = read_dexpi20(root)
    assert len(again.connections) == len(doc.connections)
    assert len(again.items) == len(doc.items)


def test_there_is_no_anchor_node_in_the_output(dexpi_files, tmp_path):
    """The Proteus ``-DefaultNode`` is a symbol placement, which DEXPI 2.0 keeps in the diagram.

    Converting drops it -- which is exactly why ``graph_signature`` numbers an item's *process*
    nodes rather than its raw ordinals.
    """
    proteus = read_dexpi(dexpi_files / "tiny_two_equipment_proteus.xml")
    assert any(node.is_anchor for _, node in ((i, n) for i in proteus.items.values() for n in i.nodes))

    text = to_text(to_element(proteus, "dexpi20"))
    assert "DefaultNode" not in text

    converted = read_dexpi20(ET.fromstring(text))
    assert not any(node.is_anchor for item in converted.items.values() for node in item.nodes)
    assert graph_signature(converted) == graph_signature(proteus)


def test_the_class_type_is_the_qualified_name_the_format_uses(dexpi_files):
    """``type="Plant/ProcessEquipment.Tank"``: one slash for the model, dots inside it."""
    root = write_dexpi20(read_dexpi(dexpi_files / "tiny_two_equipment_proteus.xml"))

    types = {element.get("type") for element in root.iter("Object")}
    assert "Plant/ProcessEquipment.Tank" in types
    assert "Plant/Piping.BallValve" in types
    assert "Plant/Piping.PipingNode" in types


def test_a_physical_quantity_round_trips_through_the_aggregate_form(dexpi_files, tmp_path):
    """A value with a unit is a ``PhysicalQuantity`` aggregate, not a literal."""
    doc = read_dexpi(dexpi_files / "unit_separator_dexpi20.xml")
    pressure = next(a for a in doc.items["Separator-1"].attributes if a.name == "UpperLimitDesignPressure")
    assert (pressure.value, pressure.units, pressure.format) == ("10", "Bar", "double")

    again = read_dexpi(write_dexpi(doc, tmp_path / "quantity.xml"))
    assert [(a.name, a.value, a.units, a.format) for a in again.items["Separator-1"].attributes] == [
        (a.name, a.value, a.units, a.format) for a in doc.items["Separator-1"].attributes
    ]
