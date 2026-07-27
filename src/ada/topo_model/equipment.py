"""Demo equipment archetypes with realistic port layouts.

A pump takes process suction in from the side, discharges up, is fed
electrical power and exposes a control-signal connection; a tank has a process
inlet/outlet and a signal (level) connection; a switchboard takes an incoming
mains supply and feeds one outgoing electrical feeder, so an electrical system
is two-ended (switchboard feeder -> pump power) just like a piping run. All are
plain :class:`ada.Equipment` instances — the archetype is just the port layout
plus a simple box body so the equipment renders.
"""

from __future__ import annotations

from typing import Callable, Iterable

import ada
from ada.api.systems import Port, PortDirection

__all__ = [
    "EQUIPMENT_ARCHETYPES",
    "build_equipment_from_catalog",
    "create_pump",
    "create_switchboard",
    "create_tank",
    "list_equipment_types",
]


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


def create_switchboard(
    name: str,
    origin: Iterable[float],
    mass: float = 800.0,
    lx: float = 0.8,
    ly: float = 0.4,
    lz: float = 1.2,
) -> ada.Equipment:
    """An electrical switchboard / distribution-board archetype: a mains supply
    in (top) and one outgoing feeder out (+X side) that powers downstream loads.
    Pairing a switchboard's ``feeder`` (OUT) with a pump's ``power`` (IN) makes
    the electrical system two-ended — a proper routed run rather than a dangling
    supply stub."""
    eq = ada.Equipment(
        name,
        mass,
        cog=(0, 0, lz / 2),
        origin=origin,
        lx=lx,
        ly=ly,
        lz=lz,
        ifc_element_class="IfcElectricDistributionBoard",
    )
    eq.add_port(Port("incoming", (0, 0, lz), (0, 0, 1), PortDirection.IN, "electrical"))
    eq.add_port(Port("feeder", (lx / 2, 0, lz / 2), (1, 0, 0), PortDirection.OUT, "electrical"))
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
    "switchboard": create_switchboard,
}


def list_equipment_types() -> list[str]:
    return sorted(EQUIPMENT_ARCHETYPES)


_PORT_DIRECTIONS = {
    "IN": PortDirection.IN,
    "OUT": PortDirection.OUT,
    "INOUT": PortDirection.INOUT,
}


def build_equipment_from_catalog(
    name: str,
    origin: Iterable[float],
    catalog_doc: dict,
    lx: float | None = None,
    ly: float | None = None,
    lz: float | None = None,
    add_body: bool = True,
) -> ada.Equipment:
    """Build an :class:`ada.Equipment` from a per-scope catalog document (see
    ``ada.comms.rest.catalog``): its bbox/mass/IFC class and its port/nozzle
    list. Explicit ``lx/ly/lz`` (from the placed cell) override the catalog
    bbox; ports come straight from the catalog doc (local nozzle positions +
    outward directions). ``add_body=False`` omits the placeholder box body — used
    when the compiled model splices in the linked CAD geometry instead."""
    bbox = catalog_doc.get("bbox") or {}
    lx = float(lx if lx is not None else bbox.get("lx", 1.0))
    ly = float(ly if ly is not None else bbox.get("ly", 1.0))
    lz = float(lz if lz is not None else bbox.get("lz", 1.0))
    mass = float(catalog_doc.get("mass", 1000.0))
    cog = catalog_doc.get("cog") or (0.0, 0.0, lz / 2)
    ifc_class = catalog_doc.get("ifc_element_class") or "IfcBuildingElementProxy"

    eq = ada.Equipment(name, mass, cog=cog, origin=origin, lx=lx, ly=ly, lz=lz, ifc_element_class=ifc_class)
    for spec in catalog_doc.get("ports") or []:
        direction = _PORT_DIRECTIONS.get(str(spec.get("direction", "INOUT")).upper(), PortDirection.INOUT)
        eq.add_port(
            Port(
                spec["name"],
                tuple(spec.get("position", (0, 0, 0))),
                tuple(spec.get("direction_vector", (0, 0, 1))),
                direction,
                spec.get("category", "process"),
            )
        )
    if add_body:
        _add_body(eq, name)
    return eq
