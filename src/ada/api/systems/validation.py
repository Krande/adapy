"""Missing-I/O reporting: find equipment ports not yet connected to a system,
and enumerate a model's site-boundary inputs/outputs.

``find_unconnected_ports`` walks a part tree; ``format_port_report`` renders the
findings as an aligned console table. ``site_interfaces`` collects the
site-boundary terminals across a set of systems; ``format_site_interfaces``
renders them. The demo prints both every run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from .categories import PortCategory
from .ports import Port, PortDirection

if TYPE_CHECKING:
    from ada import Part

    from .base import System

__all__ = [
    "PortIssue",
    "SiteInterface",
    "find_unconnected_ports",
    "equipments_with_missing_io",
    "format_port_report",
    "site_interfaces",
    "format_site_interfaces",
]


@dataclass
class PortIssue:
    equipment_name: str
    port_name: str
    category: PortCategory
    direction: PortDirection


def _iter_equipment(root: Part):
    from ada.api.spatial.equipment import Equipment

    if isinstance(root, Equipment):
        yield root
    for part in root.get_all_parts_in_assembly():
        if isinstance(part, Equipment):
            yield part


def find_unconnected_ports(root: Part) -> list[PortIssue]:
    """All ports in the tree under ``root`` that have no connected system."""
    issues = []
    for eq in _iter_equipment(root):
        for port in eq.ports:
            if not port.is_connected:
                issues.append(PortIssue(eq.name, port.name, port.category, port.direction))
    return issues


def equipments_with_missing_io(root: Part) -> dict[str, list[Port]]:
    """Equipment name -> its unconnected ports (only equipment with gaps)."""
    out: dict[str, list[Port]] = {}
    for eq in _iter_equipment(root):
        missing = eq.unconnected_ports()
        if missing:
            out[eq.name] = missing
    return out


def format_port_report(issues: list[PortIssue]) -> str:
    """Render port issues as an aligned console table."""
    if not issues:
        return "All equipment ports are connected."
    headers = ("Equipment", "Port", "Category", "Direction")
    rows = [(i.equipment_name, i.port_name, i.category, i.direction.value) for i in issues]
    return _render_table(headers, rows)


@dataclass
class SiteInterface:
    """A system's connection to the site boundary — a model input or output."""

    system_name: str
    name: str
    category: PortCategory
    direction: PortDirection  # IN = site input (into the model), OUT = site output
    position: tuple[float, float, float]

    @property
    def flow(self) -> str:
        return "input" if self.direction == PortDirection.IN else "output"


def site_interfaces(systems: Iterable[System]) -> list[SiteInterface]:
    """All site-boundary inputs/outputs across ``systems`` (e.g.
    ``assembly.systems``), in system-then-terminal order."""
    out: list[SiteInterface] = []
    for system in systems:
        for port in system.site_connections:
            out.append(
                SiteInterface(
                    system.name,
                    port.name,
                    port.category,
                    port.direction,
                    tuple(float(v) for v in port.position),
                )
            )
    return out


def format_site_interfaces(interfaces: list[SiteInterface]) -> str:
    """Render site inputs/outputs as an aligned console table."""
    if not interfaces:
        return "No site inputs/outputs defined."
    headers = ("System", "Terminal", "Flow", "Category", "Position")
    rows = [
        (
            i.system_name,
            i.name,
            i.flow,
            i.category,
            "({:.2f}, {:.2f}, {:.2f})".format(*i.position),
        )
        for i in interfaces
    ]
    return _render_table(headers, rows)


def _render_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(headers[c]), *(len(r[c]) for r in rows)) for c in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines.extend(fmt.format(*r) for r in rows)
    return "\n".join(lines)
