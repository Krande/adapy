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
    # site terminals are boundary interfaces, not equipment
    assert feed.connected_equipment == [pump]


def test_connect_site_requires_in_or_out():
    with pytest.raises(ValueError, match="must be IN"):
        ada.PipingSystem("Drain").connect_site("t", (0, 0, 0), ada.PortDirection.INOUT)


def test_site_interfaces_report():
    drain = ada.PipingSystem("Drain").connect_site("to_sea", (10, 0, 0), ada.PortDirection.OUT)
    interfaces = site_interfaces([drain])
    assert len(interfaces) == 1
    assert (interfaces[0].system_name, interfaces[0].name, interfaces[0].flow) == ("Drain", "to_sea", "output")

    txt = format_site_interfaces(interfaces)
    assert "Drain" in txt and "to_sea" in txt and "output" in txt
    assert format_site_interfaces([]) == "No site inputs/outputs defined."
