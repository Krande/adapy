"""Demo equipment archetypes with realistic port layouts.

A pump takes process suction in from the side, discharges up, is fed
electrical power and exposes a control-signal connection; a tank has a process
inlet/outlet and a signal (level) connection. Both are plain
:class:`ada.Equipment` instances — the archetype is just the port layout plus a
simple box body so the equipment renders.
"""

from __future__ import annotations

from typing import Callable, Iterable

import ada
from ada.api.systems import Port, PortDirection

__all__ = ["EQUIPMENT_ARCHETYPES", "create_pump", "create_tank", "list_equipment_types"]


def _add_body(eq: ada.Equipment, name: str) -> None:
    # origin = base center; the body box spans it in plan and rises lz
    ox, oy, oz = (float(v) for v in eq.origin)
    lo = (ox - eq.lx / 2, oy - eq.ly / 2, oz)
    hi = (ox + eq.lx / 2, oy + eq.ly / 2, oz + eq.lz)
    eq.add_object(ada.PrimBox(f"{name}_body", lo, hi))


def create_pump(
    name: str,
    origin: Iterable[float],
    mass: float = 1000.0,
    lx: float = 1.0,
    ly: float = 1.0,
    lz: float = 1.0,
) -> ada.Equipment:
    """A centrifugal-pump archetype: suction in (-X side), discharge out (top),
    electrical power in (+X side) and a control signal (INOUT, +Y side)."""
    eq = ada.Equipment(name, mass, cog=(0, 0, lz / 2), origin=origin, lx=lx, ly=ly, lz=lz, ifc_element_class="IfcPump")
    eq.add_port(Port("suction", (-lx / 2, 0, lz / 2), (-1, 0, 0), PortDirection.IN, "process"))
    eq.add_port(Port("discharge", (0, 0, lz), (0, 0, 1), PortDirection.OUT, "process"))
    eq.add_port(Port("power", (lx / 2, 0, lz / 2), (1, 0, 0), PortDirection.IN, "electrical"))
    eq.add_port(Port("signal", (0, ly / 2, lz / 2), (0, 1, 0), PortDirection.INOUT, "signal"))
    _add_body(eq, name)
    return eq


def create_tank(
    name: str,
    origin: Iterable[float],
    mass: float = 5000.0,
    lx: float = 2.0,
    ly: float = 2.0,
    lz: float = 2.0,
) -> ada.Equipment:
    """A storage-tank archetype: process inlet (top), outlet (-X side, low) and
    a level-signal connection (INOUT, +Y side)."""
    eq = ada.Equipment(name, mass, cog=(0, 0, lz / 2), origin=origin, lx=lx, ly=ly, lz=lz, ifc_element_class="IfcTank")
    eq.add_port(Port("inlet", (0, 0, lz), (0, 0, 1), PortDirection.IN, "process"))
    eq.add_port(Port("outlet", (-lx / 2, 0, 0.2), (-1, 0, 0), PortDirection.OUT, "process"))
    eq.add_port(Port("signal", (0, ly / 2, lz / 2), (0, 1, 0), PortDirection.INOUT, "signal"))
    _add_body(eq, name)
    return eq


# Named equipment archetypes buildable from a plain (name, origin, lx, ly, lz)
# footprint. Workers advertise these names so the viewer's cellbuilder can
# offer a typed "add equipment" dropdown; the procedural compiler maps a cell
# tagged with an archetype name back to the factory (ports + IFC class
# included).
EQUIPMENT_ARCHETYPES: dict[str, Callable[..., ada.Equipment]] = {
    "pump": create_pump,
    "tank": create_tank,
}


def list_equipment_types() -> list[str]:
    return sorted(EQUIPMENT_ARCHETYPES)
