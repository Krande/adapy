"""The Proteus writer, and the one thing about it that no round-trip test can protect.

Two claims are made here, and they are not the same claim.

**T1, lossless echo.** ``file -> doc -> XML -> doc'`` compares equal under
:func:`~ada.cadit.dexpi.canonical.canonicalize`, over both generated Proteus fixtures *and* the two
official CC BY 4.0 test cases vendored under ``files/dexpi_files/vendor/``. Byte identity is never
asserted -- attribute order, self-closing form, float formatting and the recomputed counters all
differ legitimately.

**The 0-based positional index.** T1 cannot see this. Proteus addresses a connection point by its
position in the owner's ``<ConnectionPoints>``, counting the symbol anchor as slot 0, and a writer
that emitted 1-based indices would produce documents that read back through adapy's own reader
unchanged -- index 1 is in range for every node list -- while wiring every connection in every file
to a symbol anchor for every other consumer of DEXPI in the world.
:func:`test_a_nozzle_to_nozzle_connection_is_written_0_based` asserts the literal integer in the
output text for exactly that reason, twice: once for a document that came from a file, and once for
one built in Python, so the assertion cannot be satisfied by echoing the input.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi import canonicalize, read_dexpi, validate_document
from ada.cadit.dexpi.model import (
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiHeader,
    DexpiItem,
    DexpiNode,
    ItemKind,
)
from ada.cadit.dexpi.read import read_proteus
from ada.cadit.dexpi.write import to_element, write_dexpi, write_proteus
from ada.cadit.dexpi.write.xml_utils import to_text

# Two nozzles wired directly to each other -- the shape of the official ``P01 Pipe FromTo Nozzles``
# test case, and the smallest document in which the positional index can be got wrong.
NOZZLE_TO_NOZZLE = """
<PlantModel>
  <PlantInformation OriginatingSystem="Test Kit" SchemaVersion="4.1.1" Units="mm"/>
  <PipingNetworkSystem ID="PipingNetworkSystem-1" ComponentClass="PipingNetworkSystem">
    <PipingNetworkSegment ID="PipingNetworkSegment-1" ComponentClass="PipingNetworkSegment">
      <Connection FromID="Nozzle-1" FromNode="1" ToID="Nozzle-2" ToNode="1"/>
    </PipingNetworkSegment>
  </PipingNetworkSystem>
  <Equipment ID="Tank-1" ComponentClass="Tank">
    <Nozzle ID="Nozzle-1" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="2">
        <Node ID="Nozzle-1-DefaultNode"/>
        <Node ID="PipingNode-1" Type="process"/>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <Equipment ID="Tank-2" ComponentClass="Tank">
    <Nozzle ID="Nozzle-2" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="2">
        <Node ID="Nozzle-2-DefaultNode"/>
        <Node ID="PipingNode-2" Type="process"/>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
</PlantModel>
"""

# The generated Proteus fixtures plus the two vendored official files: everything this writer is
# held to T1 on.
PROTEUS_FILES = (
    "tiny_two_equipment_proteus.xml",
    "unit_separator_proteus.xml",
    "vendor/P01V01-VER.EX01.xml",
    "vendor/E01V02-VER.EX01.xml",
)


@pytest.fixture
def dexpi_files(example_files):
    return example_files / "dexpi_files"


def _round_trip(path, tmp_path):
    """``file -> doc -> XML -> doc'``, returning both documents."""
    doc = read_dexpi(path)
    written = write_dexpi(doc, tmp_path / path.name)
    return doc, read_dexpi(written)


# -- the 0-based index ---------------------------------------------------------------------------------


def _from_scratch_nozzles() -> DexpiDocument:
    """Two nozzles, each with a ``-DefaultNode`` anchor at ordinal 1 and one process node.

    Built in Python rather than parsed, so the assertion below cannot be satisfied by a writer that
    merely echoes what it read.
    """
    doc = DexpiDocument(flavour=DexpiFlavour.PROTEUS, header=DexpiHeader(units="mm"))

    for index in (1, 2):
        doc.add(
            DexpiItem(
                id=f"Tank-{index}",
                class_name="Tank",
                kind=ItemKind.EQUIPMENT,
                composition_role="Equipment",
            )
        )
        doc.add(
            DexpiItem(
                id=f"Nozzle-{index}",
                class_name="Nozzle",
                kind=ItemKind.NOZZLE,
                composition_role="Nozzle",
                nodes=[
                    DexpiNode(id=f"Nozzle-{index}-DefaultNode", ordinal=1, owner_id=f"Nozzle-{index}", is_anchor=True),
                    DexpiNode(
                        id=f"PipingNode-{index}",
                        ordinal=2,
                        owner_id=f"Nozzle-{index}",
                        node_type="process",
                    ),
                ],
            ),
            parent_id=f"Tank-{index}",
        )

    doc.connections.append(
        DexpiConnection(from_item="Nozzle-1", from_node="PipingNode-1", to_item="Nozzle-2", to_node="PipingNode-2")
    )
    return doc


@pytest.mark.parametrize("build", [lambda: read_proteus(ET.fromstring(NOZZLE_TO_NOZZLE)), _from_scratch_nozzles])
def test_a_nozzle_to_nozzle_connection_is_written_0_based(build):
    """The process node of a two-node nozzle is addressed as **1**, not 2.

    Slot 0 is the ``-DefaultNode`` anchor. This is the convention every one of the 35 official DEXPI
    1.3 test cases uses, and the one adapy's reader tries first. Emitting 2 here would still parse
    back correctly through this repository and be wrong everywhere else.
    """
    doc = build()
    text = to_text(write_proteus(doc))

    assert 'FromNode="1"' in text
    assert 'ToNode="1"' in text
    assert 'FromNode="2"' not in text and 'ToNode="2"' not in text

    connection = ET.fromstring(text).find(".//Connection")
    assert connection.attrib == {
        "FromID": "Nozzle-1",
        "FromNode": "1",
        "ToID": "Nozzle-2",
        "ToNode": "1",
    }


def test_the_index_that_is_written_really_is_the_process_node():
    """Guard the guard: index 1 must resolve back to the process node, not to the anchor."""
    doc = read_proteus(ET.fromstring(to_text(write_proteus(_from_scratch_nozzles()))))

    connection = doc.connections[0]
    assert (connection.from_node, connection.to_node) == ("PipingNode-1", "PipingNode-2")
    assert doc.items["Nozzle-1"].node_by_id("PipingNode-1").is_anchor is False
    assert doc.warnings == []


def test_flow_indices_are_0_based_too():
    """``@FlowIn``/``@FlowOut`` are the same kind of index and must agree with the same convention.

    A three-node valve whose anchor is slot 0 flows in at 1 and out at 2 -- which is literally what
    the official ``C03V04-VER.EX02.xml`` writes over nodes named ``-Node-0``/``-Node-1``/``-Node-2``.
    """
    doc = read_dexpi(
        ET.fromstring(
            """
            <PlantModel>
              <PipingComponent ID="BallValve-1" ComponentClass="BallValve">
                <ConnectionPoints FlowIn="1" FlowOut="2" NumPoints="3">
                  <Node ID="BallValve-1-Node-0"/>
                  <Node ID="BallValve-1-Node-1" Type="process"/>
                  <Node ID="BallValve-1-Node-2" Type="process"/>
                </ConnectionPoints>
              </PipingComponent>
            </PlantModel>
            """
        )
    )

    points = write_proteus(doc).find(".//ConnectionPoints")
    assert (points.get("FlowIn"), points.get("FlowOut")) == ("1", "2")


# -- T1: the lossless echo ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROTEUS_FILES)
def test_t1_a_proteus_file_survives_a_write_and_a_re_read(name, dexpi_files, tmp_path):
    """The round-trip claim, over the generated fixtures and two real official files."""
    doc, again = _round_trip(dexpi_files / name, tmp_path)

    assert again.warnings == []
    assert validate_document(again) == []
    assert canonicalize(again) == canonicalize(doc)


@pytest.mark.parametrize("name", PROTEUS_FILES)
def test_the_written_file_is_reproducible(name, dexpi_files, tmp_path):
    """Two writes of the same document are the same bytes -- which is what makes the checked-in
    fixtures comparable at all."""
    doc = read_dexpi(dexpi_files / name)
    assert to_text(write_proteus(doc)) == to_text(write_proteus(doc))


def test_the_round_trip_is_not_vacuous(dexpi_files, tmp_path):
    """An edit to the document must show up in the re-read, or T1 proves nothing."""
    doc = read_dexpi(dexpi_files / "tiny_two_equipment_proteus.xml")
    doc.connections[0].to_node = "BallValve-1-Node-2"

    again = read_dexpi(write_dexpi(doc, tmp_path / "edited.xml"))
    assert again.connections[0].to_node == "BallValve-1-Node-2"
    assert canonicalize(again) != canonicalize(read_dexpi(dexpi_files / "tiny_two_equipment_proteus.xml"))


# -- recomputed counters --------------------------------------------------------------------------------


def test_the_counters_are_recomputed_from_the_children_not_echoed():
    """``@NumPoints`` and ``@Number`` are advisory in the wild and wrong often enough that the
    reader ignores them. A writer that copied them forward would propagate the error."""
    doc = read_proteus(
        ET.fromstring(
            """
            <PlantModel>
              <Equipment ID="Tank-1" ComponentClass="Tank">
                <GenericAttributes Set="DexpiAttributes" Number="99">
                  <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="T-100"/>
                </GenericAttributes>
                <Nozzle ID="Nozzle-1" ComponentClass="Nozzle">
                  <ConnectionPoints NumPoints="7">
                    <Node ID="Nozzle-1-DefaultNode"/>
                    <Node ID="PipingNode-1" Type="process"/>
                  </ConnectionPoints>
                </Nozzle>
              </Equipment>
            </PlantModel>
            """
        )
    )
    root = write_proteus(doc)

    assert root.find(".//GenericAttributes").get("Number") == "1"
    assert root.find(".//ConnectionPoints").get("NumPoints") == "2"


# -- what is echoed, and what is not ----------------------------------------------------------------------


def test_everything_the_model_does_not_hold_is_echoed_verbatim(dexpi_files, tmp_path):
    """Labels, symbols, the shape catalogue and the drawing survive a re-write.

    They are the two thirds of a real Proteus file that adapy has no concept of, and dropping them
    would make every export a downgrade of its own input.
    """
    doc = read_dexpi(dexpi_files / "unit_separator_proteus.xml")
    text = write_dexpi(doc, tmp_path / "echo.xml").read_text(encoding="utf-8")

    assert '<ShapeCatalogue Name="AdapyExampleSymbols">' in text
    assert '<Drawing Name="Separator package" Type="PID">' in text

    again = read_dexpi(tmp_path / "echo.xml")
    assert [extra.tag for extra in again.extras] == [extra.tag for extra in doc.extras]


def test_an_unmodelled_child_element_is_carried_across(tmp_path):
    """A ``<CenterLine>`` is not in the neutral model, and the official P01 file has one."""
    doc = read_proteus(
        ET.fromstring(
            """
            <PlantModel>
              <PipingNetworkSystem ID="PipingNetworkSystem-1" ComponentClass="PipingNetworkSystem">
                <PipingNetworkSegment ID="PipingNetworkSegment-1" ComponentClass="PipingNetworkSegment">
                  <CenterLine NumPoints="0"/>
                </PipingNetworkSegment>
              </PipingNetworkSystem>
            </PlantModel>
            """
        )
    )
    assert write_proteus(doc).find(".//CenterLine") is not None


def test_component_class_uri_is_echoed_from_a_proteus_source(dexpi_files):
    """A merge write keeps the source's RDL URI, because it is a real one."""
    doc = read_dexpi(dexpi_files / "vendor" / "P01V01-VER.EX01.xml")
    root = write_proteus(doc)

    pump = root.find(".//*[@ID='CentrifugalPump-1']")
    assert pump.get("ComponentClassURI") == "http://data.posccaesar.org/rdl/RDS416834"


def test_component_class_uri_is_omitted_rather_than_synthesised(dexpi_files):
    """A from-scratch write emits no ``ComponentClassURI`` at all.

    Proteus wants a POSC Caesar RDL URI there. The vendored class table has none -- the
    specification references RDL by symbol and the symbol table ships with a toolchain adapy does
    not carry -- so there is nothing to derive one from, and a plausible-looking wrong URI is worse
    than a missing one.
    """
    text = (dexpi_files / "tiny_two_equipment_proteus.xml").read_text(encoding="utf-8")
    assert "ComponentClassURI" not in text

    # Nor is the DEXPI 2.0 model URI smuggled in when converting a 2.0 document to Proteus.
    converted = to_text(to_element(read_dexpi(dexpi_files / "tiny_two_equipment_dexpi20.xml"), "proteus"))
    assert "ComponentClassURI" not in converted
    assert "data.dexpi.org" not in converted


def test_a_from_scratch_document_names_adapy_as_its_originating_system():
    """And nothing else: never a machine, a user or an organisation."""
    header = write_proteus(_from_scratch_nozzles()).find("PlantInformation")
    assert header.get("OriginatingSystem") == "adapy"
    assert header.get("Date") is None and header.get("Time") is None


# -- connections carried inside an echoed subtree ------------------------------------------------
#
# Found by running the full official corpus (scripts/fetch_dexpi_testcases.py), which no checked-in
# fixture covered: 14 of its 220 files wrote a connection twice. Both shapes below are reduced from
# real DEXPI 1.2 files, so the regression is pinned without needing the opt-in corpus.

SIGNAL_IN_AN_INFORMATION_FLOW = """
<PlantModel>
  <PlantInformation OriginatingSystem="Test Kit" SchemaVersion="4.1.1" Units="mm"/>
  <ProcessInstrumentationFunction ID="PIF-1" ComponentClass="ProcessInstrumentationFunction">
    <ConnectionPoints NumPoints="1">
      <Node ID="PIF-1-Node-1"><Position><Location X="0" Y="0" Z="0"/></Position></Node>
    </ConnectionPoints>
    <InformationFlow ID="InformationFlow-1">
      <Connection FromID="Nozzle-1" ToID="PIF-1"/>
    </InformationFlow>
  </ProcessInstrumentationFunction>
  <Equipment ID="Equipment-1" ComponentClass="Tank">
    <Nozzle ID="Nozzle-1" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="1">
        <Node ID="Nozzle-1-Node-1"><Position><Location X="0" Y="0" Z="0"/></Position></Node>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
</PlantModel>
"""


def test_a_connection_inside_an_echoed_element_is_not_also_written_at_document_level():
    """``<InformationFlow>`` is not in the neutral model, so it is echoed verbatim -- with the
    ``<Connection>`` nested in it.

    The connection is also in ``doc.connections``, and emitting it from the model as well wrote it
    twice: once inside the echoed flow and once on the root. The duplicate reads back as a second,
    owner-less edge, so the P&ID gains connectivity it never had.
    """
    doc = read_proteus(ET.fromstring(SIGNAL_IN_AN_INFORMATION_FLOW))
    assert len(doc.connections) == 1
    assert doc.connections[0].owner_id == "InformationFlow-1"

    root = write_proteus(doc)

    assert len(root.findall(".//Connection")) == 1
    assert root.find("Connection") is None, "the echoed copy is the only one; nothing on the root"
    assert root.find(".//InformationFlow/Connection") is not None


def test_the_echoed_connection_survives_a_re_read_with_its_owner():
    """Deduplicating must not degrade to dropping: the edge still comes back, still owned."""
    doc = read_proteus(ET.fromstring(SIGNAL_IN_AN_INFORMATION_FLOW))
    again = read_proteus(write_proteus(doc))

    assert canonicalize(again) == canonicalize(doc)
    assert [(c.from_item, c.to_item, c.owner_id) for c in again.connections] == [
        ("Nozzle-1", "PIF-1", "InformationFlow-1")
    ]


def test_a_document_level_connection_is_still_written_on_the_root():
    """The other half of the rule: an edge that no echoed element carries must still be emitted."""
    doc = read_proteus(
        ET.fromstring(
            """
            <PlantModel>
              <Equipment ID="Equipment-1" ComponentClass="Tank">
                <ConnectionPoints NumPoints="1">
                  <Node ID="Equipment-1-Node-1"><Position><Location X="0" Y="0" Z="0"/></Position></Node>
                </ConnectionPoints>
              </Equipment>
              <Connection FromID="Equipment-1" ToID="Equipment-1"/>
            </PlantModel>
            """
        )
    )
    root = write_proteus(doc)

    assert root.find("Connection") is not None
    assert len(root.findall(".//Connection")) == 1


def test_a_component_class_written_as_a_full_rdl_uri_round_trips():
    """``ComponentClass="http://sandbox.dexpi.org/rdl/ProcessInstrumentationFunction"``.

    Neither flavour is supposed to put a URI there, but emitters do. Resolving the last *dot* first
    landed inside the host name and produced the class ``org/rdl/ProcessInstrumentationFunction``,
    which the writer emitted and the reader then resolved differently on the way back in -- so the
    document changed class on an unedited round-trip.
    """
    doc = read_proteus(
        ET.fromstring(
            """
            <PlantModel>
              <ProcessInstrumentationFunction ID="PIF-1"
                  ComponentClass="http://sandbox.dexpi.org/rdl/ProcessInstrumentationFunction"/>
            </PlantModel>
            """
        )
    )
    assert doc.items["PIF-1"].class_name == "ProcessInstrumentationFunction"

    again = read_proteus(write_proteus(doc))
    assert canonicalize(again) == canonicalize(doc)
