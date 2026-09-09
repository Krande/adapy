"""Ports/systems wiring: bidirectional refs, fluent connect, fail-fast errors,
and the missing-I/O report."""

from __future__ import annotations

import pytest

import ada
from ada.api.systems import (
    equipments_with_missing_io,
    find_unconnected_ports,
    format_port_report,
    format_site_interfaces,
    site_interfaces,
)


def _pump():
    from ada.topo_model import create_pump

    return create_pump("P1", origin=(1, 2, 3))


def test_backward_compatible_equipment():
    eq = ada.Equipment("e", 1.0, (0, 0, 0), (0, 0, 0), 1, 1, 1)
    assert eq.ports == []
    assert eq.unconnected_ports() == []


def test_port_coercion_and_global_position():
    eq = ada.Equipment("e", 1.0, (0, 0, 0), (1, 2, 3), 1, 1, 1)
    port = eq.add_port(ada.Port("out", (0.5, 0, 1), (0, 0, 1), ada.PortDirection.OUT))
    assert isinstance(port.position, ada.Point)
    assert isinstance(port.direction_vector, ada.Direction)
    assert port.parent is eq
    assert tuple(port.get_global_position()) == (1.5, 2.0, 4.0)


def test_port_process_identity_fields_default_to_empty():
    port = ada.Port("out", (0, 0, 0), (0, 0, 1))
    assert port.tag is None
    assert port.nominal_diameter is None
    assert port.spec is None
    assert port.metadata == {}
    # each Port gets its own metadata dict, not a shared class-level one
    port.metadata["line"] = "100-PL-001"
    assert ada.Port("in", (0, 0, 0), (0, 0, 1)).metadata == {}


def test_port_positional_construction_still_works():
    """The process-identity fields were appended after ``guid``, so every
    pre-existing positional call site keeps its meaning."""
    port = ada.Port("out", (0, 0, 1), (0, 0, 1), ada.PortDirection.OUT, "electrical")
    assert (port.name, port.direction, port.category) == ("out", ada.PortDirection.OUT, "electrical")
    assert tuple(port.position) == (0.0, 0.0, 1.0)
    assert port.tag is None
    # ... and the repr is unchanged by the additions
    assert repr(port) == "Port('out', category='electrical', direction=OUT, parent=None, connected_system=None)"


def test_port_carries_source_identity():
    port = ada.Port("N1", (0, 0, 1), (0, 0, 1), tag="V-201-N1", nominal_diameter=0.15, spec="CS150")
    assert (port.tag, port.nominal_diameter, port.spec) == ("V-201-N1", 0.15, "CS150")


def test_equipment_forwards_metadata_and_guid_to_root():
    eq = ada.Equipment(
        "V-201", 1.0, (0, 0, 0), (0, 0, 0), 1, 1, 1, tag="V-201", metadata={"source": "unit"}, guid="0" * 22
    )
    assert eq.guid == "0" * 22
    assert eq.metadata == {"source": "unit"}
    assert eq.tag == "V-201"
    # omitting them keeps the pre-existing behaviour: a fresh guid, empty metadata
    plain = ada.Equipment("e", 1.0, (0, 0, 0), (0, 0, 0), 1, 1, 1)
    assert plain.guid and plain.guid != eq.guid
    assert plain.metadata == {} and plain.tag is None


def test_all_ports_includes_nested_equipment():
    """A vessel's sub-compartment hangs its own nozzles one level down; the
    vessel's full nozzle list must include them."""
    vessel = ada.Equipment("V-201", 1.0, (0, 0, 0), (0, 0, 0), 2, 2, 5)
    vessel.add_port(ada.Port("inlet", (0, 0, 5), (0, 0, 1), ada.PortDirection.IN))
    boot = ada.Equipment("V-201-BOOT", 1.0, (0, 0, 0), (0, 0, -1), 1, 1, 1)
    boot.add_port(ada.Port("water_out", (0, 0, -1), (0, 0, -1), ada.PortDirection.OUT))
    vessel / boot

    assert [p.name for p in vessel.all_ports()] == ["inlet", "water_out"]
    assert [p.name for p in vessel.all_ports(include_nested=False)] == ["inlet"]
    assert [p.name for p in vessel.ports] == ["inlet"]


def test_system_segments_default_empty_and_record_components():
    from ada.api.systems import SystemSegment

    sys1 = ada.PipingSystem("CW")
    assert sys1.segments == []

    pump = _pump()
    seg = SystemSegment("100/1", from_port=pump.get_port("discharge"), to_port=None)
    assert (seg.name, seg.to_port) == ("100/1", None)
    assert seg.components == [] and seg.metadata == {}

    seg.components.append({"class": "BallValve", "tag": "HV-101"})
    sys1.segments.append(seg)
    assert [s.name for s in sys1.segments] == ["100/1"]
    # a second segment starts from its own empty component list
    assert SystemSegment("100/2").components == []
    assert ada.SystemSegment is SystemSegment


def test_get_port_lists_available_names():
    pump = _pump()
    with pytest.raises(KeyError, match="suction"):
        pump.get_port("dischrge")


def test_fluent_connect_bidirectional_refs():
    pump = _pump()
    sys1 = ada.PipingSystem("CW", medium="water")
    ret = sys1.connect(pump, "discharge")
    assert ret is sys1
    port = pump.get_port("discharge")
    assert port.connected_system is sys1
    assert port.is_connected
    assert port in sys1.ports
    assert sys1.connected_equipment == [pump]


def test_connect_category_mismatch_raises():
    pump = _pump()
    with pytest.raises(ValueError, match="electrical"):
        ada.PipingSystem("CW").connect(pump, "power")


def test_connect_already_connected_raises():
    pump = _pump()
    ada.PipingSystem("CW").connect(pump, "discharge")
    with pytest.raises(ValueError, match="already connected"):
        ada.PipingSystem("CW2").connect(pump, "discharge")


def test_electrical_system_voltage_default():
    sys1 = ada.ElectricalSystem("Feed")
    assert sys1.voltage is ada.Voltage.LV_400
    assert sys1.category == "electrical"
    assert ada.CableSystem("Sig").category == "signal"


def test_missing_io_report():
    pump = _pump()
    ada.PipingSystem("CW").connect(pump, "discharge")
    root = ada.Assembly("A") / (ada.Part("Eq") / pump)

    issues = find_unconnected_ports(root)
    assert {(i.equipment_name, i.port_name) for i in issues} == {
        ("P1", "suction"),
        ("P1", "power"),
        ("P1", "signal"),
    }

    missing = equipments_with_missing_io(root)
    assert set(missing) == {"P1"}
    assert {p.name for p in missing["P1"]} == {"suction", "power", "signal"}

    report = format_port_report(issues)
    assert "P1" in report and "suction" in report and "Equipment" in report
    assert format_port_report([]) == "All equipment ports are connected."


def test_switchboard_archetype_two_ended_electrical():
    from ada.topo_model import create_pump, create_switchboard

    pump = create_pump("P", origin=(3, 0, 0))
    sb = create_switchboard("SB", origin=(0, 0, 0))
    assert sb.ifc_element_class == "IfcElectricDistributionBoard"
    # the outgoing feeder powers the pump — two real equipment ends, no stub
    power = ada.ElectricalSystem("PF").connect(sb, "feeder").connect(pump, "power")
    assert [p.name for p in power.ports] == ["feeder", "power"]
    assert power.connected_equipment == [sb, pump]


def test_connect_site_input_and_output():
    pump = _pump()
    feed = ada.ElectricalSystem("Feed").connect_site("grid", (0, 0, 5), ada.PortDirection.IN).connect(pump, "power")
    site = feed.site_connections
    assert [p.name for p in site] == ["grid"]
    terminal = site[0]
    assert terminal.is_site and terminal.parent is None
    assert terminal.connected_system is feed
    # a parent-less site terminal reports its raw world position
    assert tuple(terminal.get_global_position()) == (0.0, 0.0, 5.0)
    # a site terminal defaults to a +Z outward orientation when unspecified
    assert tuple(terminal.direction_vector) == (0.0, 0.0, 1.0)
    # site terminals are boundary interfaces, not equipment
    assert feed.connected_equipment == [pump]


def test_connect_site_orientation_vector():
    # A terminal on a vertical wall (x=0) faces +X into the model; the outward
    # nozzle vector is carried on the site Port so routing leaves the boundary
    # along it rather than the default +Z.
    drain = ada.PipingSystem("Drain").connect_site(
        "to_sea", (0, 4, 1), ada.PortDirection.OUT, direction_vector=(1, 0, 0)
    )
    terminal = drain.site_connections[0]
    assert tuple(terminal.direction_vector) == (1.0, 0.0, 0.0)


def test_connect_site_requires_in_or_out():
    with pytest.raises(ValueError, match="must be IN"):
        ada.PipingSystem("Drain").connect_site("t", (0, 0, 0), ada.PortDirection.INOUT)


def test_code_specs_are_catalog_shaped():
    """The worker-advertised code specs must validate as catalog docs so the API
    can list them (with origin) and sync them into the DB catalog."""
    from ada.api.systems import list_system_types, system_type_specs
    from ada.comms.rest.catalog import validate_equipment_doc, validate_system_doc
    from ada.topo_model.equipment import equipment_archetype_specs

    eq_specs = {s["slug"]: s for s in equipment_archetype_specs()}
    assert {"pump", "tank", "switchboard"} <= set(eq_specs)
    assert eq_specs["switchboard"]["doc"]["ifc_element_class"] == "IfcElectricDistributionBoard"
    for s in eq_specs.values():
        validate_equipment_doc(s["doc"])  # raises on invalid
    # a code archetype and a catalog doc must expose the same port shape, so the
    # process-identity fields are advertised even when the archetype leaves them unset
    pump_port = eq_specs["pump"]["doc"]["ports"][0]
    assert {"tag", "nominal_diameter", "spec"} <= set(pump_port)
    assert (pump_port["tag"], pump_port["nominal_diameter"], pump_port["spec"]) == (None, None, None)

    assert list_system_types() == ["piping", "duct", "cable", "electrical"]
    sys_specs = {s["slug"]: s for s in system_type_specs()}
    assert sys_specs["electrical"]["doc"]["voltage"] == 400
    assert sys_specs["piping"]["doc"]["voltage"] is None
    for s in sys_specs.values():
        validate_system_doc(s["doc"])  # raises on invalid


def test_site_interfaces_report():
    drain = ada.PipingSystem("Drain").connect_site("to_sea", (10, 0, 0), ada.PortDirection.OUT)
    interfaces = site_interfaces([drain])
    assert len(interfaces) == 1
    assert (interfaces[0].system_name, interfaces[0].name, interfaces[0].flow) == ("Drain", "to_sea", "output")

    txt = format_site_interfaces(interfaces)
    assert "Drain" in txt and "to_sea" in txt and "output" in txt
    assert format_site_interfaces([]) == "No site inputs/outputs defined."
