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

import math
from typing import Callable, Iterable

import numpy as np

import ada
from ada.api.systems import Port, PortDirection

__all__ = [
    "EQUIPMENT_ARCHETYPES",
    "apply_equipment_rotation",
    "build_equipment_from_catalog",
    "create_exhaust_fan",
    "create_hvac",
    "create_pump",
    "create_switchboard",
    "create_tank",
    "equipment_archetype_specs",
    "list_equipment_types",
    "rotation_matrix",
]


# Standard cable-entry height for side-mounted electrical ports (power/feeder),
# measured from the equipment base. Real switchgear brings cabling in at a common
# tray height, not each unit's own mid-height — so an electrical run between two
# differently-tall units (switchboard feeder -> pump power) stays level and its
# tray needs no sub-radius vertical jog to bridge a height mismatch. Kept below
# the shortest archetype (the 0.6 m exhaust fan) so it always lands on the body.
_CABLE_ENTRY_Z = 0.5


def _add_body(eq: ada.Equipment, name: str) -> None:
    # origin = base center; the body box spans it in plan and rises lz
    ox, oy, oz = (float(v) for v in eq.origin)
    lo = (ox - eq.lx / 2, oy - eq.ly / 2, oz)
    hi = (ox + eq.lx / 2, oy + eq.ly / 2, oz + eq.lz)
    eq.add_object(ada.PrimBox(f"{name}_body", lo, hi))


def rotation_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray | None:
    """Intrinsic X→Y→Z rotation matrix for the given per-axis degrees, or
    ``None`` when there is nothing to rotate (all zero). The columns are the
    rotated body axes; composed Rz·Ry·Rx so ROT_Z is the dominant, gravity-safe
    yaw applied last."""
    if not (rx_deg or ry_deg or rz_deg):
        return None
    rx, ry, rz = (math.radians(a) for a in (rx_deg, ry_deg, rz_deg))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    my = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    mz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return mz @ my @ mx


def _oriented_box(name: str, p1, p2, color, rot: np.ndarray, pivot: np.ndarray) -> ada.Shape:
    """An axis-aligned box (corners ``p1``/``p2``) re-expressed as an oriented
    :class:`ada.geom.solids.Box` rotated by ``rot`` about ``pivot``. Used to spin
    an equipment's placeholder body without leaving the analytic path (the NGEOM
    stream honours the box's placement axes, unlike ``PrimBox`` which bakes only
    a translation)."""
    from ada.core.guid import create_guid
    from ada.geom import Geometry
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.solids import Box

    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    lengths = np.abs(p2 - p1)
    lo = np.minimum(p1, p2)
    corner = pivot + rot @ (lo - pivot)
    x_dir = rot @ np.array([1.0, 0.0, 0.0])
    z_dir = rot @ np.array([0.0, 0.0, 1.0])
    box = Box(
        Axis2Placement3D(Point(*corner), Direction(*z_dir), Direction(*x_dir)),
        float(lengths[0]),
        float(lengths[1]),
        float(lengths[2]),
    )
    return ada.Shape(name, geom=Geometry(create_guid(), box, None), color=color)


def apply_equipment_rotation(eq: ada.Equipment, rx_deg: float, ry_deg: float, rz_deg: float) -> ada.Equipment:
    """Rotate a built equipment in place about its footprint centre (``origin``):
    its ports (nozzle positions + outward directions) and its placeholder box
    body all spin together so routing connects to the correctly-oriented side and
    the rendered body matches. A no-op when all angles are zero. Ports keep their
    identity (routing holds references to them); the body boxes are swapped for
    oriented equivalents."""
    from ada import Direction, Point

    rot = rotation_matrix(rx_deg, ry_deg, rz_deg)
    if rot is None:
        return eq
    pivot = np.asarray(eq.origin, dtype=float)
    for port in eq.ports:
        pos = np.asarray(port.position, dtype=float)
        dv = np.asarray(port.direction_vector, dtype=float)
        port.position = Point(*(rot @ pos))
        port.direction_vector = Direction(*(rot @ dv))
    rotated = []
    for shp in eq.shapes:
        if isinstance(shp, ada.PrimBox):
            ob = _oriented_box(shp.name, shp.p1, shp.p2, shp.color, rot, pivot)
            # Preserve parentage — the swapped-in Shape must stay owned by the
            # equipment, else it exports as a parent-less object (IFC export raises).
            ob.parent = eq
            rotated.append(ob)
        else:
            rotated.append(shp)
    eq._shapes[:] = rotated
    return eq


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
    eq.add_port(Port("power", (lx / 2, 0, _CABLE_ENTRY_Z), (1, 0, 0), PortDirection.IN, "electrical"))
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
    eq.add_port(Port("feeder", (lx / 2, 0, _CABLE_ENTRY_Z), (1, 0, 0), PortDirection.OUT, "electrical"))
    # A second outgoing feeder so one board can supply several loads — e.g. feed
    # a downstream switchboard on another deck as well as a local load.
    eq.add_port(Port("feeder2", (-lx / 2, 0, _CABLE_ENTRY_Z), (-1, 0, 0), PortDirection.OUT, "electrical"))
    _add_body(eq, name)
    return eq


def create_hvac(
    name: str,
    origin: Iterable[float],
    mass: float = 1200.0,
    lx: float = 1.5,
    ly: float = 1.0,
    lz: float = 1.2,
) -> ada.Equipment:
    """An HVAC air-handling unit: conditioned-air supply out (top, feeds a duct
    run), electrical power in (+X side) and a control signal (INOUT, +Y side).
    Its ``supply`` port is category ``process`` — a :class:`DuctSystem` routes off
    the system type, not the port category, so a duct connects it to a roof
    exhaust just like a pipe connects two process ports."""
    eq = ada.Equipment(
        name, mass, cog=(0, 0, lz / 2), origin=origin, lx=lx, ly=ly, lz=lz, ifc_element_class="IfcUnitaryEquipment"
    )
    eq.add_port(Port("supply", (0, 0, lz), (0, 0, 1), PortDirection.OUT, "process"))
    eq.add_port(Port("power", (lx / 2, 0, _CABLE_ENTRY_Z), (1, 0, 0), PortDirection.IN, "electrical"))
    eq.add_port(Port("signal", (0, ly / 2, lz / 2), (0, 1, 0), PortDirection.INOUT, "signal"))
    _add_body(eq, name)
    return eq


def create_exhaust_fan(
    name: str,
    origin: Iterable[float],
    mass: float = 300.0,
    lx: float = 0.8,
    ly: float = 0.8,
    lz: float = 0.6,
) -> ada.Equipment:
    """A roof-mounted exhaust fan: a duct intake underneath (-Z, where the HVAC
    supply duct terminates) and electrical power in (+X side). Sits on top of the
    structure so the duct climbs out of the room and up to it."""
    eq = ada.Equipment(name, mass, cog=(0, 0, lz / 2), origin=origin, lx=lx, ly=ly, lz=lz, ifc_element_class="IfcFan")
    eq.add_port(Port("intake", (0, 0, 0), (0, 0, -1), PortDirection.IN, "process"))
    eq.add_port(Port("power", (lx / 2, 0, min(_CABLE_ENTRY_Z, lz / 2)), (1, 0, 0), PortDirection.IN, "electrical"))
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
    "hvac": create_hvac,
    "exhaust_fan": create_exhaust_fan,
}


def list_equipment_types() -> list[str]:
    return sorted(EQUIPMENT_ARCHETYPES)


def equipment_archetype_specs() -> list[dict]:
    """Each built-in archetype as a catalog-shaped spec ``{slug, name, doc}``
    (the ``doc`` mirrors ``ada.comms.rest.catalog`` equipment docs: bbox / mass /
    cog / IFC class / ports). Workers advertise these so the API can list code
    archetypes with their origin and "sync" one into the per-scope DB catalog
    without importing ``ada`` in the slim API image."""
    specs: list[dict] = []
    for slug, factory in EQUIPMENT_ARCHETYPES.items():
        eq = factory(slug, (0.0, 0.0, 0.0))
        specs.append(
            {
                "slug": slug,
                "name": slug.replace("_", " ").title(),
                "doc": {
                    "bbox": {"lx": float(eq.lx), "ly": float(eq.ly), "lz": float(eq.lz)},
                    "mass": float(eq.mass),
                    "cog": [float(v) for v in eq.cog] if eq.cog is not None else None,
                    "ifc_element_class": eq.ifc_element_class,
                    "ports": [
                        {
                            "name": p.name,
                            "position": [float(v) for v in p.position],
                            "direction_vector": [float(v) for v in p.direction_vector],
                            "direction": p.direction.value,
                            "category": p.category,
                        }
                        for p in eq.ports
                    ],
                },
            }
        )
    return specs


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
