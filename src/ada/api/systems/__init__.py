"""Systems and ports: logical service networks (piping/duct/cable/electrical)
that equipment ports connect to, plus missing-I/O validation."""

from .base import CableSystem, DuctSystem, ElectricalSystem, PipingSystem, System
from .categories import PortCategory, Voltage
from .ports import Port, PortDirection
from .validation import (
    PortIssue,
    equipments_with_missing_io,
    find_unconnected_ports,
    format_port_report,
)

__all__ = [
    "CableSystem",
    "DuctSystem",
    "ElectricalSystem",
    "PipingSystem",
    "Port",
    "PortCategory",
    "PortDirection",
    "PortIssue",
    "System",
    "Voltage",
    "equipments_with_missing_io",
    "find_unconnected_ports",
    "format_port_report",
]
