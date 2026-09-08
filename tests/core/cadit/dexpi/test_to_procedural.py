"""DEXPI P&ID -> procedural document.

The P&ID below is hand-authored inline; the shared example files land in their own PR. It is a
small separator unit, sized to exercise the parts of the conversion that can go wrong quietly:

* a ``Chamber`` (the separator's boot) whose nozzles have to become ports on the *vessel*;
* one line carrying two segments, i.e. a branch, which must become two independent runs because
  ``route_system`` has no branch support;
* an in-line ball valve, which must not be mistaken for an end of its segment;
* three off-page connectors, which must become site terminals with the direction their **class**
  gives them (``connect_site`` refuses ``INOUT``);
* nozzles on both ends of every run, so a port name that does not match the catalog, or a category
  that does not match the system, is visible here rather than as a system the compiler silently
  drops three steps later.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi.model import ItemKind
from ada.cadit.dexpi.read.read_proteus import read_proteus
from ada.cadit.dexpi.read.to_procedural import (
    DexpiImportReport,
    default_layout_rules,
    dexpi_to_procedural_doc,
)
from ada.topo_model.layout import LayoutRules, validate_equipment_in_cells

#: Deck bounds that put the separator and the exchanger on one deck and the two pumps on the next,
#: so the generated model has a real second storey and runs that cross it.
UNIT_LAYOUT = LayoutRules(max_length=16.0, max_width=6.0, deck_height=5.0)


def _nozzle(nozzle_id: str, tag: str, flow: str | None, dn: str | None = "100") -> str:
    """A Proteus ``<Nozzle>`` with the ``-DefaultNode`` anchor at ordinal 1, so every positional
    reference into it is off by one unless the reader knows."""
    attributes = [f'<GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="{tag}"/>']
    if dn:
        attributes.append(
            '<GenericAttribute Name="NominalDiameterNumericalValueRepresentationAssignmentClass" '
            f'Format="double" Value="{dn}"/>'
            '<GenericAttribute Name="NominalDiameterTypeRepresentationAssignmentClass" '
            'Format="string" Value="DN"/>'
        )
    points = {"in": ' FlowIn="1"', "out": ' FlowOut="1"', None: ""}[flow]
    return f"""
    <Nozzle ID="{nozzle_id}" ComponentClass="Nozzle">
      <GenericAttributes Set="DexpiAttributes">{"".join(attributes)}</GenericAttributes>
      <ConnectionPoints NumPoints="2"{points}>
        <Node ID="{nozzle_id}-DefaultNode"/>
        <Node ID="{nozzle_id}-Node" Type="process"/>
      </ConnectionPoints>
    </Nozzle>"""


def _segment(system_id: str, line: str, number: str, medium: str, body: str) -> str:
    return f"""
  <PipingNetworkSystem ID="{system_id}" ComponentClass="PipingNetworkSystem">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="LineNumberAssignmentClass" Format="string" Value="{line}"/>
      <GenericAttribute Name="FluidCodeAssignmentClass" Format="string" Value="{medium}"/>
      <GenericAttribute Name="PipingClassCodeAssignmentClass" Format="string" Value="CS150"/>
    </GenericAttributes>
    <PipingNetworkSegment ID="{system_id}-S{number}" ComponentClass="PipingNetworkSegment">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SegmentNumberAssignmentClass" Format="string" Value="{number}"/>
      </GenericAttributes>{body}
    </PipingNetworkSegment>
  </PipingNetworkSystem>"""


def _off_page(connector_id: str, tag: str, direction: str) -> str:
    """An off-page connector, composed INTO its segment the way a real Proteus file writes it."""
    return f"""
      <PipingComponent ID="{connector_id}" ComponentClass="Flow{direction}PipeOffPageConnector">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="{tag}"/>
        </GenericAttributes>
        <ConnectionPoints NumPoints="2">
          <Node ID="{connector_id}-DefaultNode"/>
          <Node ID="{connector_id}-Node" Type="process"/>
        </ConnectionPoints>
      </PipingComponent>"""


def _connection(from_id: str, to_id: str, from_node: str = "1", to_node: str = "1") -> str:
    """One ``<Connection>``. The node indices are **0-based** and every ``<ConnectionPoints>`` here
    opens with its symbol anchor, so ``1`` is the first process node."""
    return f'\n      <Connection FromID="{from_id}" FromNode="{from_node}" ToID="{to_id}" ToNode="{to_node}"/>'


_VALVE = """
      <PipingComponent ID="Valve-1" ComponentClass="BallValve">
        <GenericAttributes Set="DexpiAttributes">
          <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="HV-2021"/>
          <GenericAttribute Name="NominalDiameterNumericalValueRepresentationAssignmentClass"
                            Format="double" Value="100"/>
          <GenericAttribute Name="NominalDiameterTypeRepresentationAssignmentClass"
                            Format="string" Value="DN"/>
        </GenericAttributes>
        <ConnectionPoints NumPoints="3" FlowIn="1" FlowOut="2">
          <Node ID="Valve-1-Node-0"/>
          <Node ID="Valve-1-Node-1" Type="process"/>
          <Node ID="Valve-1-Node-2" Type="process"/>
        </ConnectionPoints>
      </PipingComponent>"""

_SEGMENTS = "".join(
    [
        # Feed in from off the sheet.
        _segment(
            "PNS-101",
            "L-101",
            "1",
            "HC",
            _off_page("OPC-1", "OPC-01", "In") + _connection("OPC-1", "Equipment-1-N1"),
        ),
        # The branch: line L-201 runs to both spared pumps, as two two-ended segments.
        _segment("PNS-201", "L-201", "1", "HC", _connection("Equipment-1-N3", "Equipment-2-N1")),
        _segment("PNS-201B", "L-201", "2", "HC", _connection("Equipment-1-N4", "Equipment-3-N1")),
        # A run with an in-line valve: the segment's ends are the two nozzles, not the valve.
        _segment(
            "PNS-202",
            "L-202",
            "1",
            "HC",
            _VALVE + _connection("Equipment-2-N2", "Valve-1") + _connection("Valve-1", "Equipment-4-N1", from_node="2"),
        ),
        _segment(
            "PNS-203",
            "L-203",
            "1",
            "HC",
            _off_page("OPC-2", "OPC-02", "Out") + _connection("Equipment-4-N2", "OPC-2"),
        ),
        _segment("PNS-204", "L-204", "1", "HC", _connection("Equipment-3-N2", "Equipment-4-N3")),
        _segment(
            "PNS-205",
            "L-205",
            "1",
            "HC",
            _off_page("OPC-3", "OPC-03", "Out") + _connection("Equipment-4-N4", "OPC-3"),
        ),
    ]
)


UNIT_PID = f"""<?xml version="1.0" encoding="utf-8"?>
<PlantModel>
  <PlantInformation Application="adapy" Date="2026-02-03" Time="10:11:12" OriginatingSystem="adapy test kit"
                    OriginatingSystemVendor="adapy" OriginatingSystemVersion="1.0"
                    SchemaVersion="4.1.1" Units="mm" ProjectName="SeparatorUnit">
    <UnitsOfMeasure/>
  </PlantInformation>

  <Equipment ID="Equipment-1" ComponentClass="Separator">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="V-201"/>
      <GenericAttribute Name="PipingClassCodeAssignmentClass" Format="string" Value="CS150"/>
    </GenericAttributes>
    <Position><Location X="0.30" Y="0.42" Z="0"/><Axis X="0" Y="0" Z="1"/></Position>
    {_nozzle("Equipment-1-N1", "N1", "in", dn="150")}
    <Equipment ID="Equipment-1-Boot" ComponentClass="Chamber">
      <GenericAttributes Set="DexpiAttributes">
        <GenericAttribute Name="SubTagNameAssignmentClass" Format="string" Value="BOOT"/>
      </GenericAttributes>
      {_nozzle("Equipment-1-N3", "N3", "out")}
      {_nozzle("Equipment-1-N4", "N4", "out")}
    </Equipment>
  </Equipment>

  <Equipment ID="Equipment-2" ComponentClass="CentrifugalPump">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="P-201A"/>
    </GenericAttributes>
    {_nozzle("Equipment-2-N1", "S", "in")}
    {_nozzle("Equipment-2-N2", "D", "out")}
  </Equipment>

  <Equipment ID="Equipment-3" ComponentClass="CentrifugalPump">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="P-201B"/>
    </GenericAttributes>
    {_nozzle("Equipment-3-N1", "S", "in")}
    {_nozzle("Equipment-3-N2", "D", "out")}
  </Equipment>

  <Equipment ID="Equipment-4" ComponentClass="TubularHeatExchanger">
    <GenericAttributes Set="DexpiAttributes">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="E-201"/>
    </GenericAttributes>
    {_nozzle("Equipment-4-N1", "T1", "in")}
    {_nozzle("Equipment-4-N2", "T2", "out")}
    {_nozzle("Equipment-4-N3", "S1", "in")}
    {_nozzle("Equipment-4-N4", "S2", "out")}
  </Equipment>
{_SEGMENTS}
  <ShapeCatalogue Name="symbols">
    <Equipment ID="SEPARATOR_SHAPE" ComponentName="SEPARATOR_SHAPE"/>
  </ShapeCatalogue>
  <Drawing Name="sheet-1"/>
</PlantModel>
"""

#: The seven runs the unit P&ID describes: five between equipment, two to the site boundary.
UNIT_SYSTEMS = ["L-101/1", "L-201/1", "L-201/2", "L-202/1", "L-203/1", "L-204/1", "L-205/1"]

#: The four placed items. ``BOOT`` is a chamber and must NOT appear -- it folds into ``V-201``.
UNIT_EQUIPMENT = ["V-201", "P-201A", "P-201B", "E-201"]


def write_unit_pid(tmp_path: pathlib.Path) -> pathlib.Path:
    """The unit P&ID on disk, for the tests that go through ``ada.from_dexpi``."""
    path = tmp_path / "separator_unit.xml"
    path.write_text(UNIT_PID, encoding="utf-8")
    return path


@pytest.fixture
def unit():
    return read_proteus(ET.fromstring(UNIT_PID))


@pytest.fixture
def converted(unit):
    return dexpi_to_procedural_doc(unit, layout=UNIT_LAYOUT)


def _system(doc: dict, name: str) -> dict:
    return next(spec for spec in doc["systems"] if spec["NAME"] == name)


def _report(doc: dict) -> DexpiImportReport:
    return DexpiImportReport.from_dict(doc["dexpi"]["report"])


# --------------------------------------------------------------------------- #
# The document parses cleanly to begin with
# --------------------------------------------------------------------------- #
def test_the_unit_pid_reads_without_warnings(unit):
    assert unit.warnings == []


# --------------------------------------------------------------------------- #
# Segment-per-system decomposition
# --------------------------------------------------------------------------- #
def test_one_system_per_dexpi_segment(converted):
    doc, _ = converted
    assert sorted(spec["NAME"] for spec in doc["systems"]) == sorted(UNIT_SYSTEMS)
    assert all(len(spec["CONNECTIONS"]) == 2 for spec in doc["systems"])


def test_a_branch_becomes_two_independent_runs_off_one_line(converted):
    """``route_system`` routes ``ports[0] -> ports[-1]`` and knows nothing about tees, so a line
    that feeds two pumps has to arrive as two two-ended runs, not one three-ended one."""
    doc, _ = converted
    branch = [spec for spec in doc["systems"] if spec["NAME"].startswith("L-201/")]
    assert [spec["NAME"] for spec in branch] == ["L-201/1", "L-201/2"]
    reached = {conn["EQUIPMENT"] for spec in branch for conn in spec["CONNECTIONS"]}
    assert reached == {"V-201", "P-201A", "P-201B"}


def test_the_parent_network_survives_as_medium_and_provenance(converted):
    doc, _ = converted
    spec = _system(doc, "L-202/1")
    assert spec["MEDIUM"] == "HC"
    assert spec["TYPE"] == "piping"
    dexpi = spec["METADATA"]["dexpi"]
    assert dexpi["network_class"] == "PipingNetworkSystem"
    assert dexpi["line_number"] == "L-202"
    assert dexpi["segment_number"] == "1"


def test_an_inline_component_is_not_mistaken_for_an_end_of_its_segment(converted):
    """The valve sits between the pump and the exchanger; the run's ends are the two nozzles."""
    doc, _ = converted
    spec = _system(doc, "L-202/1")
    assert [(c["EQUIPMENT"], c["PORT"]) for c in spec["CONNECTIONS"]] == [
        ("P-201A", "d"),
        ("E-201", "t1"),
    ]


# --------------------------------------------------------------------------- #
# Off-page connectors -> site terminals
# --------------------------------------------------------------------------- #
def test_an_off_page_connector_becomes_a_site_terminal_with_its_class_direction(converted):
    """``FlowInPipeOffPageConnector`` is a ``PipingSourceItem`` and ``FlowOut...`` a
    ``PipingTargetItem``, so the concrete class settles the direction -- and it has to, because
    ``connect_site`` rejects ``INOUT``."""
    doc, _ = converted
    inlet = _system(doc, "L-101/1")["CONNECTIONS"][0]
    outlet = _system(doc, "L-203/1")["CONNECTIONS"][1]

    assert inlet["SITE"] == "opc-01" and inlet["DIRECTION"] == "IN"
    assert outlet["SITE"] == "opc-02" and outlet["DIRECTION"] == "OUT"
    assert all(conn.get("DIRECTION") in (None, "IN", "OUT") for spec in doc["systems"] for conn in spec["CONNECTIONS"])


def test_site_terminals_sit_on_a_deck_boundary_facing_inward(converted):
    """A P&ID gives a connector no coordinate at all, so one is generated: inputs on the deck's
    ``-X`` face, outputs on its ``+X`` face, with the vector pointing into the model -- the
    direction the run leaves the boundary along."""
    doc, _ = converted
    deck = next(space for space in doc["spaces"] if space["NAME"] == "Deck1")

    inlet = _system(doc, "L-101/1")["CONNECTIONS"][0]
    assert inlet["POSITION"][0] == pytest.approx(deck["X"])
    assert inlet["DIRECTION_VECTOR"] == [1.0, 0.0, 0.0]

    outlets = [_system(doc, name)["CONNECTIONS"][1] for name in ("L-203/1", "L-205/1")]
    for outlet in outlets:
        assert outlet["POSITION"][0] == pytest.approx(deck["X"] + deck["DX"])
        assert outlet["DIRECTION_VECTOR"] == [-1.0, 0.0, 0.0]
    # Two outputs on one face must not land on the same point, or the two runs route as one.
    assert outlets[0]["POSITION"] != outlets[1]["POSITION"]


# --------------------------------------------------------------------------- #
# The end-to-end name match
# --------------------------------------------------------------------------- #
def test_every_connection_names_a_port_the_catalog_actually_has(converted):
    """The nozzle -> port name map is the whole wiring contract: ``_wire_systems`` looks the port
    up by name and drops the entire system with a warning when it is not there."""
    doc, catalog = converted
    slug_of = {row["NAME"]: row["DESCRIPTION"] for row in doc["equipments"]}

    named = [
        (conn["EQUIPMENT"], conn["PORT"])
        for spec in doc["systems"]
        for conn in spec["CONNECTIONS"]
        if conn.get("EQUIPMENT")
    ]
    assert len(named) == 11  # every endpoint but the three site terminals
    for equipment, port in named:
        ports = catalog[slug_of[equipment]]["ports"]
        assert port in [p["name"] for p in ports], f"{equipment}.{port} is not in its catalog document"


def test_every_connected_port_is_a_process_port(converted):
    """A category mismatch makes ``System.connect`` raise and the compiler drop the whole system
    with only a warning -- the single most effective way to lose half a P&ID quietly."""
    doc, catalog = converted
    slug_of = {row["NAME"]: row["DESCRIPTION"] for row in doc["equipments"]}
    for spec in doc["systems"]:
        for conn in spec["CONNECTIONS"]:
            if not conn.get("EQUIPMENT"):
                continue
            port = next(p for p in catalog[slug_of[conn["EQUIPMENT"]]]["ports"] if p["name"] == conn["PORT"])
            assert port["category"] == "process"


def test_a_chambers_nozzles_are_ports_on_the_vessel_that_owns_it(converted):
    """The separator's boot is a sub-volume of the separator, not an asset of its own: it is not
    placed, and its two outlets are ports on ``V-201``."""
    doc, catalog = converted
    assert sorted(row["NAME"] for row in doc["equipments"]) == sorted(UNIT_EQUIPMENT)

    vessel = catalog[next(row["DESCRIPTION"] for row in doc["equipments"] if row["NAME"] == "V-201")]
    assert sorted(p["tag"] for p in vessel["ports"]) == ["N1", "N3", "N4"]


# --------------------------------------------------------------------------- #
# Placement
# --------------------------------------------------------------------------- #
def test_every_placement_carries_its_own_extents(converted):
    """``_equipment_to_object`` calls ``_require_coords(eq, ("X","Y","Z","LX","LY","LZ"))`` before
    anything reaches the catalog, so the catalog-bbox fallback is unreachable from the compiler and
    the layout has to stamp the extents itself."""
    doc, catalog = converted
    for row in doc["equipments"]:
        assert all(row.get(key) is not None for key in ("X", "Y", "Z", "LX", "LY", "LZ")), row["NAME"]
        bbox = catalog[row["DESCRIPTION"]]["bbox"]
        assert (row["LX"], row["LY"], row["LZ"]) == pytest.approx((bbox["lx"], bbox["ly"], bbox["lz"]))


def test_every_equipment_lands_in_a_cell(converted):
    doc, _ = converted
    assert validate_equipment_in_cells(doc) == []
    assert _report(doc).is_clean


def test_the_deck_pitch_is_the_explicit_height_not_the_tallest_item(unit):
    """Deck pitch is uniform across the plan, so one 15 m ``ProcessColumn`` would otherwise make
    every deck 16 m tall. The importer's default rules name a height for exactly that reason."""
    assert default_layout_rules().deck_height == 6.0

    tall = read_proteus(ET.fromstring(UNIT_PID.replace('ComponentClass="Separator"', 'ComponentClass="ProcessColumn"')))
    doc, _ = dexpi_to_procedural_doc(tall)
    assert {space["DZ"] for space in doc["spaces"]} == {6.0}


def test_a_base_documents_placements_win_over_the_generated_ones(converted, unit):
    """Re-importing an edited P&ID must not move the equipment somebody already positioned."""
    doc, _ = converted
    moved = dict(next(row for row in doc["equipments"] if row["NAME"] == "P-201A"))
    moved.update({"X": 2.5, "Y": 3.5, "SPACE_NAME": "Deck1"})

    merged, _ = dexpi_to_procedural_doc(unit, layout=UNIT_LAYOUT, base_doc={"equipments": [moved]})
    kept = next(row for row in merged["equipments"] if row["NAME"] == "P-201A")
    assert (kept["X"], kept["Y"], kept["SPACE_NAME"]) == (2.5, 3.5, "Deck1")


# --------------------------------------------------------------------------- #
# In-line components
# --------------------------------------------------------------------------- #
def test_inline_components_ride_on_the_run_by_default(converted):
    doc, _ = converted
    components = _system(doc, "L-202/1")["METADATA"]["dexpi"]["components"]
    assert [(c["dexpi_class"], c["tag"], c["nominal_diameter"]) for c in components] == [
        ("BallValve", "HV-2021", pytest.approx(0.1))
    ]
    assert "HV-2021" not in [row["NAME"] for row in doc["equipments"]]


def test_inline_components_can_be_materialised_as_equipment(unit):
    doc, catalog = dexpi_to_procedural_doc(unit, layout=UNIT_LAYOUT, inline_components="equipment")
    valve = next(row for row in doc["equipments"] if row["NAME"] == "HV-2021")
    assert catalog[valve["DESCRIPTION"]]["ifc_element_class"] == "IfcValve"
    # It is placed, but the run still connects its two nozzle ends -- the router has no notion of
    # where a fitting sits along a routed path.
    assert [c["EQUIPMENT"] for c in _system(doc, "L-202/1")["CONNECTIONS"]] == ["P-201A", "E-201"]
    assert validate_equipment_in_cells(doc) == []


# --------------------------------------------------------------------------- #
# Nothing is dropped quietly
# --------------------------------------------------------------------------- #
def test_a_segment_that_cannot_be_routed_is_reported_not_dropped(unit):
    """A segment whose end is an in-line component rather than a nozzle has nowhere to connect.
    It has to come back named, with a reason -- that is the whole point of the report."""
    broken = UNIT_PID.replace(
        '<Connection FromID="Valve-1" FromNode="2" ToID="Equipment-4-N1" ToNode="1"/>',
        "",
    )
    doc, _ = dexpi_to_procedural_doc(read_proteus(ET.fromstring(broken)), layout=UNIT_LAYOUT)

    report = _report(doc)
    assert not report.is_clean
    assert [issue.name for issue in report.of_kind("system")] == ["L-202/1"]
    assert report.issues[0].stage == "connectivity"
    assert "L-202/1" in report.format()
    assert "L-202/1" not in [spec["NAME"] for spec in doc["systems"]]


def test_an_oversize_item_is_reported_rather_than_left_off_the_model(unit):
    doc, _ = dexpi_to_procedural_doc(unit, layout=LayoutRules(max_length=4.0, max_width=4.0, deck_height=5.0))
    report = _report(doc)
    assert [issue.name for issue in report.of_kind("equipment")] == ["V-201", "E-201"]
    assert all(issue.stage == "layout" for issue in report.of_kind("equipment"))


def test_a_clean_report_still_says_what_it_looked_at(converted):
    doc, _ = converted
    report = _report(doc)
    assert report.stats == {"equipment": 4, "systems": 7}
    assert report.format().startswith("DEXPI import complete")


# --------------------------------------------------------------------------- #
# Flow direction
# --------------------------------------------------------------------------- #
def test_direction_falls_back_to_the_connection_when_the_node_declares_none(unit):
    """DEXPI 2.0 has no ``FlowIn``/``FlowOut`` at all -- direction is implied by which end of a
    pipe a node sits on. Without the fallback every port of a 2.0 document comes out ``INOUT`` and
    every vessel gets its feed and its draw-off on the shell."""
    flowless = read_proteus(ET.fromstring(UNIT_PID.replace(' FlowIn="1"', "").replace(' FlowOut="1"', "")))
    nozzles = [item for item in flowless.items.values() if item.kind is ItemKind.NOZZLE]
    assert nozzles and all(node.flow is None for item in nozzles for node in item.nodes)

    _, catalog = dexpi_to_procedural_doc(flowless, layout=UNIT_LAYOUT)
    vessel = next(doc for doc in catalog.values() if doc.get("tag") == "V-201")
    assert {p["tag"]: p["direction"] for p in vessel["ports"]} == {"N1": "IN", "N3": "OUT", "N4": "OUT"}
