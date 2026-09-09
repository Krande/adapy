"""Branch points: the runs that meet at a shared ``PipeTee``.

A DEXPI segment is a two-ended run and ``ada.topology.routing.route_system`` routes exactly
``ports[0] -> ports[-1]``, so one system per segment is the only faithful mapping and a 3-way
junction can never be one system. That much is by design. What is *not* by design is the case this
module pins: a passive fitting that more than one ``PipingNetworkSegment`` names as an end. Each of
the runs meeting there is an ordinary two-ended run -- it only failed to import because its end
named a fitting, which is neither a nozzle nor an equipment nor an off-page connector -- so the
importer materialises the fitting as a small equipment with one port per connection node and all of
them resolve. Before that, the flagship fixture lost 7 of its 10 runs to this.

Two halves. The first works on a hand-authored P&ID small enough to reason about: a vessel feeding
two spared pumps through one tee, one branch carrying an in-line valve so the "interior fitting" and
"junction fitting" cases sit side by side in the same file. The second holds the shipped realistic
fixture to the end-to-end claim -- that the runs at its two tees route, and that the routed pipe
geometry actually lands *on* the tee's ports rather than merely being reported as connected.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.api.spatial.equipment import Equipment
from ada.cadit.dexpi import canonicalize, read_dexpi
from ada.cadit.dexpi.equipment_list import branch_points
from ada.cadit.dexpi.model import ItemKind
from ada.cadit.dexpi.read.read_proteus import read_proteus
from ada.cadit.dexpi.read.to_procedural import (
    DexpiImportReport,
    dexpi_to_procedural_doc,
)
from ada.topo_model.layout import LayoutRules

from .test_to_procedural import _connection, _nozzle

TEE_LAYOUT = LayoutRules(max_length=16.0, max_width=6.0, deck_height=5.0)

#: The tee two of the three segments reference from outside and the third nests. Four connection
#: points: the symbol anchor at index 0 and the three process nodes at 1-3, so a 0-based index into
#: the list is what a ``<Connection>`` has to carry. Node 3 declares no flow at all -- a real tee
#: often does not -- which leaves it to the connectivity fallback.
_TEE = """
      <PipingComponent ID="Tee-1" ComponentClass="PipeTee">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="TE-301"/>
          <GenericAttribute Name="NominalDiameterNumericalValueRepresentationAssignmentClass"
                            Format="double" Value="100"/>
          <GenericAttribute Name="NominalDiameterTypeRepresentationAssignmentClass"
                            Format="string" Value="DN"/>
        </GenericAttributes>
        <ConnectionPoints NumPoints="4" FlowIn="1" FlowOut="2">
          <Node ID="Tee-1-Node-0"/>
          <Node ID="Tee-1-Node-1" Type="process"/>
          <Node ID="Tee-1-Node-2" Type="process"/>
          <Node ID="Tee-1-Node-3" Type="process"/>
        </ConnectionPoints>
      </PipingComponent>"""

#: An ordinary in-line fitting: exactly one segment names it, so it must stay interior to that run.
_GATE_VALVE = """
      <PipingComponent ID="Gate-1" ComponentClass="GateValve">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="HV-301"/>
        </GenericAttributes>
        <ConnectionPoints NumPoints="3" FlowIn="1" FlowOut="2">
          <Node ID="Gate-1-Node-0"/>
          <Node ID="Gate-1-Node-1" Type="process"/>
          <Node ID="Gate-1-Node-2" Type="process"/>
        </ConnectionPoints>
      </PipingComponent>"""

#: One ``PipingNetworkSystem`` with three segments meeting at ``Tee-1``: the vessel outlet into the
#: tee (the segment the file happens to nest the tee under), and one branch out to each pump.
TEE_PID = f"""<?xml version="1.0" encoding="utf-8"?>
<PlantModel>
  <PlantInformation Application="adapy" Date="2026-09-08" Time="00:00:00" OriginatingSystem="adapy test kit"
                    OriginatingSystemVendor="adapy" OriginatingSystemVersion="1.0"
                    SchemaVersion="4.1.1" Units="mm" ProjectName="TeeUnit">
    <UnitsOfMeasure/>
  </PlantInformation>

  <Equipment ID="Equipment-1" ComponentClass="Separator">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="V-301"/>
    </GenericAttributes>
    {_nozzle("Equipment-1-N1", "N1", "out")}
  </Equipment>

  <Equipment ID="Equipment-2" ComponentClass="CentrifugalPump">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="P-301A"/>
    </GenericAttributes>
    {_nozzle("Equipment-2-N1", "S", "in")}
  </Equipment>

  <Equipment ID="Equipment-3" ComponentClass="CentrifugalPump">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="P-301B"/>
    </GenericAttributes>
    {_nozzle("Equipment-3-N1", "S", "in")}
  </Equipment>

  <PipingNetworkSystem ID="PNS-301" ComponentClass="PipingNetworkSystem">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="LineNumberAssignmentClass" Format="string" Value="L-301"/>
      <GenericAttribute Name="FluidCodeAssignmentClass" Format="string" Value="HC"/>
    </GenericAttributes>
    <PipingNetworkSegment ID="PNS-301-S1" ComponentClass="PipingNetworkSegment">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SegmentNumberAssignmentClass" Format="string" Value="1"/>
      </GenericAttributes>{_TEE}{_connection("Equipment-1-N1", "Tee-1")}
    </PipingNetworkSegment>
    <PipingNetworkSegment ID="PNS-301-S2" ComponentClass="PipingNetworkSegment">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SegmentNumberAssignmentClass" Format="string" Value="2"/>
      </GenericAttributes>{_GATE_VALVE}{_connection("Tee-1", "Gate-1", from_node="2")}\
{_connection("Gate-1", "Equipment-2-N1", from_node="2")}
    </PipingNetworkSegment>
    <PipingNetworkSegment ID="PNS-301-S3" ComponentClass="PipingNetworkSegment">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SegmentNumberAssignmentClass" Format="string" Value="3"/>
      </GenericAttributes>{_connection("Tee-1", "Equipment-3-N1", from_node="3")}
    </PipingNetworkSegment>
  </PipingNetworkSystem>
</PlantModel>
"""


@pytest.fixture(scope="module")
def tee_doc():
    return read_proteus(ET.fromstring(TEE_PID))


@pytest.fixture(scope="module")
def converted(tee_doc):
    return dexpi_to_procedural_doc(tee_doc, layout=TEE_LAYOUT)


def _system(doc: dict, name: str) -> dict:
    return next(spec for spec in doc["systems"] if spec["NAME"] == name)


def _equipment(doc: dict, name: str) -> dict:
    return next(row for row in doc["equipments"] if row["NAME"] == name)


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #
def test_only_the_fitting_more_than_one_segment_ends_at_is_a_branch_point(tee_doc):
    """The whole distinction in one assertion. Both are ``PipingComponent``\\ s of the same network;
    only the tee is named as an end by more than one segment, and only the tee is a junction."""
    assert branch_points(tee_doc) == {"Tee-1": ["PNS-301-S1", "PNS-301-S2", "PNS-301-S3"]}


def test_a_branch_point_becomes_equipment_with_a_port_for_each_of_its_nodes(converted):
    doc, catalog = converted
    tee = _equipment(doc, "TE-301")
    document = catalog[tee["DESCRIPTION"]]
    assert document["ifc_element_class"] == "IfcPipeFitting"
    assert document["dexpi_class"] == "PipeTee"
    assert len(document["ports"]) == 3


def test_the_ordinary_inline_fitting_is_not_placed_as_equipment(converted):
    """``HV-301`` is interior to run 2. It rides on that run's metadata and nothing else -- the
    default ``inline_components="metadata"`` is unchanged by any of this."""
    doc, _ = converted
    assert [row["NAME"] for row in doc["equipments"]] == ["V-301", "P-301A", "P-301B", "TE-301"]
    components = _system(doc, "L-301/2")["METADATA"]["dexpi"]["components"]
    assert [c["tag"] for c in components] == ["HV-301"]


def test_the_segment_that_nests_the_tee_does_not_also_carry_it_as_an_interior_component(converted):
    """The file nests ``Tee-1`` under segment 1, but the run *stops* there -- so it must not also be
    listed among the fittings that run passes through, or the tee would be reported twice and
    materialised twice under ``inline_components="equipment"``."""
    doc, _ = converted
    assert _system(doc, "L-301/1")["METADATA"]["dexpi"]["components"] == []


# --------------------------------------------------------------------------- #
# What the runs connect to
# --------------------------------------------------------------------------- #
def test_every_segment_at_the_tee_becomes_its_own_two_ended_run(converted):
    doc, _ = converted
    assert [spec["NAME"] for spec in doc["systems"]] == ["L-301/1", "L-301/2", "L-301/3"]

    wiring = {spec["NAME"]: [(c["EQUIPMENT"], c["PORT"]) for c in spec["CONNECTIONS"]] for spec in doc["systems"]}
    assert wiring["L-301/1"][0] == ("V-301", "n1")
    assert wiring["L-301/2"][1] == ("P-301A", "s")
    assert wiring["L-301/3"][1] == ("P-301B", "s")
    assert [end[0] for end in (wiring["L-301/1"][1], wiring["L-301/2"][0], wiring["L-301/3"][0])] == ["TE-301"] * 3


def test_the_three_runs_take_three_different_ports_of_the_tee(converted):
    """The load-bearing detail. Resolving the tee *as a whole* would give all three runs the same
    port and route three pipes into one point; each end has to resolve to the specific connection
    node its own ``<Connection>`` named."""
    doc, catalog = converted
    used = [
        _system(doc, "L-301/1")["CONNECTIONS"][1]["PORT"],
        _system(doc, "L-301/2")["CONNECTIONS"][0]["PORT"],
        _system(doc, "L-301/3")["CONNECTIONS"][0]["PORT"],
    ]
    available = {port["name"] for port in catalog[_equipment(doc, "TE-301")["DESCRIPTION"]]["ports"]}
    assert len(set(used)) == 3
    assert set(used) == available


def test_the_branch_point_records_which_runs_meet_there(converted):
    doc, _ = converted
    assert _equipment(doc, "TE-301")["METADATA"]["dexpi"] == {
        "dexpi_id": "Tee-1",
        "dexpi_class": "PipeTee",
        "tag": "TE-301",
        "branch_point_of": ["L-301/1", "L-301/2", "L-301/3"],
    }


def test_nothing_is_reported_as_lost(converted):
    doc, _ = converted
    report = DexpiImportReport.from_dict(doc["dexpi"]["report"])
    assert report.is_clean, report.format()
    assert report.stats == {"equipment": 4, "systems": 3}


def test_the_junction_is_materialised_once_even_with_inline_components_as_equipment(tee_doc):
    """``inline_components="equipment"`` walks each run's interior fittings. The tee is not one of
    those any more, so it must not come back a second time under a deduplicated name."""
    doc, _ = dexpi_to_procedural_doc(tee_doc, layout=TEE_LAYOUT, inline_components="equipment")
    names = [row["NAME"] for row in doc["equipments"]]
    assert names == ["V-301", "P-301A", "P-301B", "TE-301", "HV-301"]
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- #
# End to end, on the shipped realistic fixture
# --------------------------------------------------------------------------- #
@pytest.fixture
def unit_separator(example_files):
    return example_files / "dexpi_files" / "unit_separator_proteus.xml"


@pytest.fixture
def model(unit_separator):
    return ada.SystemModel.from_dexpi(unit_separator)


@pytest.fixture
def built(model):
    return model.to_assembly()


def test_the_realistic_fixture_now_routes_every_run_that_has_two_ends(model, built):
    """It used to lose 7 of its 10 runs to its two tees. The one that is still reported is not a
    branch point at all: ``205/1`` is a relief valve discharging to something the P&ID does not
    draw, so it has one end and nowhere to route to.

    The gap is on the *model*: a run the P&ID never gave two ends is a reading failure, and the
    build never saw it. What the build could not carry would be on the assembly instead."""
    assert [(issue.name, issue.stage) for issue in model.report.issues] == [("205/1", "connectivity")]

    routed = {pipe.name for pipe in built.get_all_physical_objects(by_type=ada.Pipe)}
    assert routed == {
        f"{name}_route" for name in ("201/1", "202/1", "203/1", "203/2", "203/3", "204/1", "204/2", "204/3", "206/1")
    }
    assert all(pipe.segments for pipe in built.get_all_physical_objects(by_type=ada.Pipe))


def test_each_run_at_a_tee_closes_exactly_on_the_tee_port_it_names(built):
    """Report and wiring can both look right while the geometry misses. Every run that names a tee
    port must have an actual pipe end *at* that port, and each tee's three ports must be taken by
    three different runs -- otherwise two runs share a port and the third branch is dangling."""
    equipment = {part.name: part for part in built.get_all_parts_in_assembly() if isinstance(part, Equipment)}
    pipes = {pipe.name: pipe for pipe in built.get_all_physical_objects(by_type=ada.Pipe)}

    for tee_name, runs in (("TE-203", ["203/1", "203/2", "203/3"]), ("TE-204", ["204/1", "204/2", "204/3"])):
        tee = equipment[tee_name]
        ports = {port.name: np.asarray(port.get_global_position(), dtype=float) for port in tee.ports}
        assert len(ports) == 3

        touched = []
        for run in runs:
            segments = pipes[f"{run}_route"].segments
            ends = [np.asarray(segments[0].p1.p, dtype=float), np.asarray(segments[-1].p2.p, dtype=float)]
            gap, port_name = min(
                (float(np.linalg.norm(end - position)), name) for end in ends for name, position in ports.items()
            )
            assert gap == pytest.approx(0.0, abs=1e-9), f"{run} does not reach {tee_name}: {gap} m short"
            touched.append(port_name)
        assert sorted(touched) == sorted(ports), f"{tee_name} ports are not one per run: {touched}"


def test_a_materialised_tee_writes_back_as_the_pipe_tee_it_came_from(unit_separator, tmp_path):
    """The merge writer's half of the same rule. A branch point is a mirror of a piping component
    the source already carries, not equipment adapy authored -- so an unedited round-trip must leave
    the ``PipeTee`` and every connection into it untouched, and mint no ``ProcessEquipment`` for it.
    """
    source = read_dexpi(unit_separator)
    model = ada.SystemModel.from_dexpi(unit_separator)
    again = read_dexpi(model.to_dexpi(tmp_path / "roundtrip.xml"))

    before = {item["id"]: item for item in canonicalize(source)["items"]}
    after = {item["id"]: item for item in canonicalize(again)["items"]}
    for tee in ("PipeTee-1", "PipeTee-2"):
        assert after.get(tee) == before[tee], tee
        assert _connections_at(again, tee) == _connections_at(source, tee)

    assert not [item for item in again.items.values() if item.class_name == "ProcessEquipment"]
    assert set(after) - set(before) == set()


def _connections_at(doc, item_id: str) -> list[tuple]:
    return sorted(
        (c.owner_id or "", c.from_item or "", c.from_node or "", c.to_item or "", c.to_node or "")
        for c in doc.connections
        if item_id in (c.from_item, c.to_item)
    )


def test_a_branch_point_is_a_piping_component_not_an_equipment_item(unit_separator):
    """A sanity check on the premise: these really are ``PipingComponent``\\ s in the source, which
    is why ``equipment_items`` never saw them and every run into them used to be dropped."""
    source = read_dexpi(unit_separator)
    junctions = branch_points(source)
    assert set(junctions) == {"PipeTee-1", "PipeTee-2"}
    assert all(source.items[item_id].kind is ItemKind.PIPING_COMPONENT for item_id in junctions)
    assert all(len(segments) == 3 for segments in junctions.values())
