"""The convergence contract: one P&ID, two serializations, one graph.

DEXPI 2.0.0 replaced the wire format without touching the plant semantics, so a document and its
conversion say the same thing about the plant while sharing almost nothing textually -- different
root, different element names, different IDs, and connectivity addressed by position in one and by
ID in the other. The claim this file holds the two readers to is that
:func:`~ada.cadit.dexpi.canonical.graph_signature` cannot tell them apart.

Both forms of the same small unit are authored inline, side by side, so the pairing is readable:
a tank with a shell chamber and two nozzles, a pump, an off-page connector and a ball valve, wired
into two piping segments. The Proteus side deliberately opens every ``<ConnectionPoints>`` with a
``-DefaultNode`` anchor, because that is what a real emitter writes and because the anchor is the
one thing DEXPI 2.0 has no way to express -- if the contract survives it, it survives the corpus.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi import (
    DexpiFlavour,
    ItemKind,
    canonicalize,
    graph_signature,
    validate_document,
)
from ada.cadit.dexpi.read import read_dexpi20, read_proteus

PROTEUS = """
<PlantModel>
  <PlantInformation OriginatingSystem="Test Kit" OriginatingSystemVendor="Test Vendor"
                    OriginatingSystemVersion="1.0" SchemaVersion="4.1.1" Units="mm"/>
  <Equipment ID="E-1" ComponentClass="Tank">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="T-100"/>
    </GenericAttributes>
    <Equipment ID="C-1" ComponentClass="Chamber">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="Shell"/>
      </GenericAttributes>
      <Nozzle ID="N-1" ComponentClass="Nozzle">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="N1"/>
        </GenericAttributes>
        <ConnectionPoints NumPoints="2">
          <Node ID="N-1-DefaultNode"/>
          <Node ID="PN-1" Type="process">
            <GenericAttributes Set="DexpiAttributes">
              <GenericAttribute Name="NominalDiameterNumericalValueRepresentationAssignmentClass"
                                Format="string" Value="80"/>
              <GenericAttribute Name="NominalDiameterTypeRepresentationAssignmentClass"
                                Format="string" Value="DN"/>
            </GenericAttributes>
          </Node>
        </ConnectionPoints>
      </Nozzle>
    </Equipment>
    <Nozzle ID="N-2" ComponentClass="Nozzle">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="N2"/>
      </GenericAttributes>
      <ConnectionPoints NumPoints="2">
        <Node ID="N-2-DefaultNode"/>
        <Node ID="PN-2" Type="process"/>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <Equipment ID="E-2" ComponentClass="CentrifugalPump">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="P-100"/>
    </GenericAttributes>
    <Nozzle ID="N-3" ComponentClass="Nozzle">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="S"/>
      </GenericAttributes>
      <ConnectionPoints NumPoints="2">
        <Node ID="N-3-DefaultNode"/>
        <Node ID="PN-3" Type="process"/>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <PipingNetworkSystem ID="PNS-1" ComponentClass="PipingNetworkSystem">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="LineNumberAssignmentClass" Format="string" Value="100"/>
      <GenericAttribute Name="FluidCodeAssignmentClass" Format="string" Value="PW"/>
    </GenericAttributes>
    <PipingNetworkSegment ID="SEG-1" ComponentClass="PipingNetworkSegment">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SegmentNumberAssignmentClass" Format="string" Value="S1"/>
      </GenericAttributes>
      <PipingComponent ID="V-1" ComponentClass="BallValve">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="HV-100"/>
        </GenericAttributes>
        <ConnectionPoints FlowIn="1" FlowOut="2" NumPoints="3">
          <Node ID="V-1-Node-0"/>
          <Node ID="V-1-Node-1" Type="process"/>
          <Node ID="V-1-Node-2" Type="process"/>
        </ConnectionPoints>
      </PipingComponent>
      <PipeOffPageConnector ID="OPC-1" ComponentClass="FlowInPipeOffPageConnector">
        <ConnectionPoints NumPoints="2">
          <Node ID="OPC-1-DefaultNode"/>
          <Node ID="PN-4" Type="process"/>
        </ConnectionPoints>
      </PipeOffPageConnector>
      <Connection FromID="OPC-1" FromNode="1" ToID="V-1" ToNode="1"/>
      <Connection FromID="V-1" FromNode="2" ToID="N-2" ToNode="1"/>
    </PipingNetworkSegment>
    <PipingNetworkSegment ID="SEG-2" ComponentClass="PipingNetworkSegment">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SegmentNumberAssignmentClass" Format="string" Value="S2"/>
      </GenericAttributes>
      <Connection FromID="N-3" FromNode="1" ToID="N-1" ToNode="1"/>
    </PipingNetworkSegment>
  </PipingNetworkSystem>
</PlantModel>
"""

DEXPI20 = """
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
            <Components property="Chambers">
              <Object id="Chamber1" type="Plant/ProcessEquipment.Chamber">
                <Data property="SubTagName"><String>Shell</String></Data>
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
            </Components>
            <Components property="Nozzles">
              <Object id="Nozzle2" type="Plant/ProcessEquipment.Nozzle">
                <Data property="SubTagName"><String>N2</String></Data>
                <Components property="Nodes">
                  <Object id="PipingNode2" type="Plant/Piping.PipingNode"/>
                </Components>
              </Object>
            </Components>
          </Object>
          <Object id="Pump1" type="Plant/ProcessEquipment.CentrifugalPump">
            <Data property="TagName"><String>P-100</String></Data>
            <Components property="Nozzles">
              <Object id="Nozzle3" type="Plant/ProcessEquipment.Nozzle">
                <Data property="SubTagName"><String>S</String></Data>
                <Components property="Nodes">
                  <Object id="PipingNode3" type="Plant/Piping.PipingNode"/>
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
                <Components property="Items">
                  <Object id="Valve1" type="Plant/Piping.BallValve">
                    <Data property="TagName"><String>HV-100</String></Data>
                    <Components property="Nodes">
                      <Object id="PipingNode4" type="Plant/Piping.PipingNode"/>
                      <Object id="PipingNode5" type="Plant/Piping.PipingNode"/>
                    </Components>
                  </Object>
                  <Object id="OffPageConnector1" type="Plant/Piping.FlowInPipeOffPageConnector">
                    <Components property="Nodes">
                      <Object id="PipingNode6" type="Plant/Piping.PipingNode"/>
                    </Components>
                  </Object>
                </Components>
                <Components property="Connections">
                  <Object id="Pipe1" type="Plant/Piping.Pipe">
                    <References property="SourceItem" objects="#OffPageConnector1"/>
                    <References property="SourceNode" objects="#PipingNode6"/>
                    <References property="TargetItem" objects="#Valve1"/>
                    <References property="TargetNode" objects="#PipingNode4"/>
                  </Object>
                  <Object id="Pipe2" type="Plant/Piping.Pipe">
                    <References property="SourceItem" objects="#Valve1"/>
                    <References property="SourceNode" objects="#PipingNode5"/>
                    <References property="TargetItem" objects="#Nozzle2"/>
                    <References property="TargetNode" objects="#PipingNode2"/>
                  </Object>
                </Components>
              </Object>
              <Object id="Segment2" type="Plant/Piping.PipingNetworkSegment">
                <Data property="SegmentNumber"><String>S2</String></Data>
                <Components property="Connections">
                  <Object id="Pipe3" type="Plant/Piping.Pipe">
                    <References property="SourceItem" objects="#Nozzle3"/>
                    <References property="SourceNode" objects="#PipingNode3"/>
                    <References property="TargetItem" objects="#Nozzle1"/>
                    <References property="TargetNode" objects="#PipingNode1"/>
                  </Object>
                </Components>
              </Object>
            </Components>
          </Object>
        </Components>
      </Object>
    </Components>
  </Object>
</Model>
"""


@pytest.fixture
def proteus():
    return read_proteus(ET.fromstring(PROTEUS))


@pytest.fixture
def dexpi20():
    return read_dexpi20(ET.fromstring(DEXPI20))


# -- the contract ------------------------------------------------------------------------------------


def test_both_documents_are_structurally_valid(proteus, dexpi20):
    assert validate_document(proteus) == []
    assert validate_document(dexpi20) == []
    assert proteus.warnings == [] and dexpi20.warnings == []


def test_the_same_pid_in_both_flavours_has_the_same_graph_signature(proteus, dexpi20):
    assert graph_signature(proteus) == graph_signature(dexpi20)


def test_the_signature_is_not_vacuous(proteus, dexpi20):
    signature = graph_signature(proteus)

    assert signature["items"] == sorted(
        [
            "Tank[T-100]",
            "Tank[T-100]/Chamber[Shell]",
            "Tank[T-100]/Chamber[Shell]/Nozzle[N1]",
            "Tank[T-100]/Nozzle[N2]",
            "CentrifugalPump[P-100]",
            "CentrifugalPump[P-100]/Nozzle[S]",
            "PipingNetworkSystem[]",
            "PipingNetworkSystem[]/PipingNetworkSegment[]",
            "PipingNetworkSystem[]/PipingNetworkSegment[]",
            "PipingNetworkSystem[]/PipingNetworkSegment[]/BallValve[HV-100]",
            "PipingNetworkSystem[]/PipingNetworkSegment[]/FlowInPipeOffPageConnector[]",
        ]
    )
    assert signature["connections"] == sorted(
        [
            (
                "PipingNetworkSystem[]/PipingNetworkSegment[]/FlowInPipeOffPageConnector[]#1",
                "PipingNetworkSystem[]/PipingNetworkSegment[]/BallValve[HV-100]#1",
            ),
            (
                "PipingNetworkSystem[]/PipingNetworkSegment[]/BallValve[HV-100]#2",
                "Tank[T-100]/Nozzle[N2]#1",
            ),
            (
                "CentrifugalPump[P-100]/Nozzle[S]#1",
                "Tank[T-100]/Chamber[Shell]/Nozzle[N1]#1",
            ),
        ]
    )
    assert signature == graph_signature(dexpi20)


def test_the_proteus_anchor_is_excluded_rather_than_shifting_every_node(proteus, dexpi20):
    # The Proteus nozzle has two nodes and the DEXPI 2.0 one has a single node, because the anchor
    # is a symbol placement rather than a connection point and DEXPI 2.0 keeps that in the diagram.
    # Numbering the process nodes on their own is what makes both say "#1".
    assert [(n.ordinal, n.is_anchor) for n in proteus.items["N-1"].nodes] == [(1, True), (2, False)]
    assert [(n.ordinal, n.is_anchor) for n in dexpi20.items["Nozzle1"].nodes] == [(1, False)]

    assert "Tank[T-100]/Chamber[Shell]/Nozzle[N1]#1" in graph_signature(proteus)["nodes"]
    assert graph_signature(proteus)["nodes"] == graph_signature(dexpi20)["nodes"]


def test_a_rewiring_on_one_side_breaks_the_contract(proteus, dexpi20):
    # Otherwise the equality above would prove nothing: swap the valve's two ends in one flavour.
    proteus.connections[0].to_node = "V-1-Node-2"
    assert graph_signature(proteus) != graph_signature(dexpi20)


def test_a_missing_item_on_one_side_breaks_the_contract(proteus, dexpi20):
    dexpi20.items.pop("Chamber1")
    assert graph_signature(proteus) != graph_signature(dexpi20)


# -- what legitimately differs -------------------------------------------------------------------------


def test_the_ids_differ_and_canonicalize_says_so(proteus, dexpi20):
    # graph_signature is ID-free on purpose; canonicalize is not, because a write-then-read must
    # preserve IDs. The two oracles answer two different questions and must not be conflated.
    assert canonicalize(proteus) != canonicalize(dexpi20)
    assert set(proteus.items) != set(dexpi20.items)


def test_the_flavour_and_the_header_differ(proteus, dexpi20):
    assert proteus.flavour is DexpiFlavour.PROTEUS
    assert dexpi20.flavour is DexpiFlavour.DEXPI20

    # The same provenance, said in two places: Proteus writes <PlantInformation>, DEXPI 2.0 writes
    # ordinary <Data> on the model envelope.
    assert proteus.header.originating_system == dexpi20.header.originating_system == "Test Kit"
    assert proteus.header.schema_version == "4.1.1"
    assert dexpi20.header.schema_version == "2.0.0"
    assert proteus.header.imports == {} and set(dexpi20.header.imports) == {"Core", "Plant"}


def test_the_composition_role_records_what_each_flavour_actually_wrote(proteus, dexpi20):
    # Proteus distinguishes a chamber from its owner by the element tag; DEXPI 2.0 by the
    # composition property. Both are kept verbatim, and neither is part of the signature.
    assert proteus.items["C-1"].composition_role == "Equipment"
    assert dexpi20.items["Chamber1"].composition_role == "Chambers"
    assert proteus.items["V-1"].composition_role == "PipingComponent"
    assert dexpi20.items["Valve1"].composition_role == "Items"


def test_flow_direction_is_a_proteus_only_statement(proteus, dexpi20):
    # @FlowIn/@FlowOut have no DEXPI 2.0 counterpart: there, direction is implied by which end of a
    # pipe the node is. Deriving one from the other would be an inference, not a parse, so the
    # reader does not make it -- a consumer that needs direction reads it off the connections.
    assert [node.flow for node in proteus.items["V-1"].nodes] == [None, "in", "out"]
    assert [node.flow for node in dexpi20.items["Valve1"].nodes] == [None, None]


def test_the_kinds_and_the_nominal_diameters_agree(proteus, dexpi20):
    assert proteus.items["OPC-1"].kind is ItemKind.OFF_PAGE_CONNECTOR
    assert dexpi20.items["OffPageConnector1"].kind is ItemKind.OFF_PAGE_CONNECTOR

    assert proteus.items["N-1"].nodes[1].nominal_diameter == pytest.approx(0.08)
    assert dexpi20.items["Nozzle1"].nodes[0].nominal_diameter == pytest.approx(0.08)


def test_the_store_dispatches_to_the_right_reader_for_either_flavour(tmp_path):
    from ada.cadit.dexpi import DexpiStore

    proteus_path = tmp_path / "unit_proteus.xml"
    dexpi20_path = tmp_path / "unit_dexpi20.xml"
    proteus_path.write_text(PROTEUS, encoding="utf-8")
    dexpi20_path.write_text(DEXPI20, encoding="utf-8")

    left, right = DexpiStore(proteus_path), DexpiStore(dexpi20_path)

    assert (left.flavour, right.flavour) == (DexpiFlavour.PROTEUS, DexpiFlavour.DEXPI20)
    assert left.graph_signature() == right.graph_signature()
    assert left.validate() == [] and right.validate() == []
    assert [item.tag for item in left.iter_equipment()] == [item.tag for item in right.iter_equipment()]
    assert [item.tag for item in left.iter_nozzles()] == [item.tag for item in right.iter_nozzles()]
    assert len(left.connections_of("V-1")) == len(right.connections_of("Valve1")) == 2
