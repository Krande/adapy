"""Ports/systems wiring: bidirectional refs, fluent connect, fail-fast errors,
and the missing-I/O report."""

from __future__ import annotations

import pytest

import ada
from ada.api.systems import (
    equipments_with_missing_io,
    find_unconnected_ports,
    format_port_report,
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
