"""Systems: logical service networks (piping, ducting, cabling, electrical).

A ``System`` is not a :class:`ada.Part` — it is the logical object that
equipment ports connect to. Wiring reads fluently::

    cooling = PipingSystem("CoolingWater", medium="water").connect(pump, "discharge").connect(tank, "inlet")

Routing the system through a :class:`~ada.topology.grid.CellGrid` (and turning
the routed path into geometry) is delegated to ``ada.topology.routing`` via the
:meth:`System.route` convenience.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Iterable

from .categories import PortCategory, Voltage
from .ports import Port, PortDirection

if TYPE_CHECKING:
    from ada.api.spatial.equipment import Equipment
    from ada.geom.points import Point
    from ada.topology.grid import CellGrid
    from ada.topology.routing import RoutingRules

__all__ = [
    "System",
    "PipingSystem",
    "DuctSystem",
    "CableSystem",
    "ElectricalSystem",
    "SYSTEM_KINDS",
    "list_system_types",
    "system_type_specs",
]


class System:
    """Base system; subclasses fix the service ``category`` ports must match."""

    category: ClassVar[PortCategory] = "process"

    def __init__(self, name: str, medium: str | None = None, metadata: dict | None = None):
        self.name = name
        self.medium = medium
        self.metadata = metadata if metadata is not None else {}
        self.ports: list[Port] = []
        self.routed_path: list[Point] | None = None
        self.route_geometry: list = []

    def connect(self, equipment: Equipment, port_name: str) -> System:
        """Connect this system to the named port on ``equipment``. Returns
        ``self`` so connections chain fluently."""
        port = equipment.get_port(port_name)
        if port.category != self.category:
            raise ValueError(
                f"Cannot connect {type(self).__name__} {self.name!r} (category {self.category!r}) to port "
                f"{port_name!r} on equipment {equipment.name!r} (category {port.category!r})"
            )
        if port.connected_system is not None:
            raise ValueError(
                f"Port {port_name!r} on equipment {equipment.name!r} is already connected to system "
                f"{port.connected_system.name!r}; disconnect it before rewiring"
            )
        port.connected_system = self
        self.ports.append(port)
        return self

    def connect_site(
        self,
        name: str,
        position: Point | Iterable[float],
        direction: PortDirection = PortDirection.INOUT,
        direction_vector: Iterable[float] = (0, 0, 1),
    ) -> System:
        """Terminate this system at a fixed site location — a *site input* or
        *site output* — rather than an equipment port. This is where the system
        crosses the model boundary (grid supply, cooling-water make-up, a drain
        to site, …). ``position`` is a world-space point; ``direction`` must be
        ``IN`` (into the site) or ``OUT`` (out of the site). Returns ``self`` so
        it chains fluently with :meth:`connect`."""
        if direction not in (PortDirection.IN, PortDirection.OUT):
            raise ValueError(f"Site connection {name!r} must be IN (input) or OUT (output), got {direction.value!r}")
        port = Port(name, position, direction_vector, direction, self.category, is_site=True)
        port.connected_system = self
        self.ports.append(port)
        return self

    @property
    def site_connections(self) -> list[Port]:
        """The system's site-boundary terminals (inputs/outputs), in order."""
        return [p for p in self.ports if p.is_site]

    def route(self, grid: CellGrid, rules: RoutingRules | None = None) -> list:
        """Route this system through ``grid`` and generate its geometry.
        Convenience wrapper over ``ada.topology.routing`` — returns
        ``self.route_geometry``."""
        from ada.topology.routing import route_system, system_route_to_geometry

        route_system(self, grid, rules=rules)
        system_route_to_geometry(self)
        return self.route_geometry

    @property
    def connected_equipment(self) -> list[Equipment]:
        out = []
        for port in self.ports:
            if port.parent is not None and port.parent not in out:
                out.append(port.parent)
        return out

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r}, ports={[p.name for p in self.ports]})"


class PipingSystem(System):
    category: ClassVar[PortCategory] = "process"

    def __init__(
        self,
        name: str,
        medium: str | None = None,
        metadata: dict | None = None,
        pipe_radius: float = 0.05,
        pipe_wt: float = 5e-3,
    ):
        super().__init__(name, medium=medium, metadata=metadata)
        self.pipe_radius = pipe_radius
        self.pipe_wt = pipe_wt


class DuctSystem(System):
    """Routed HVAC/process ducting. Rendered as a rectangular duct (a BOX
    cross-section swept along the route), not a round pipe."""

    category: ClassVar[PortCategory] = "process"

    def __init__(
        self,
        name: str,
        medium: str | None = None,
        metadata: dict | None = None,
        duct_width: float = 0.4,
        duct_height: float = 0.3,
        wall: float = 2e-3,
        bend_radius: float | None = None,
        strict: bool = False,
    ):
        super().__init__(name, medium=medium, metadata=metadata)
        self.duct_width = duct_width
        self.duct_height = duct_height
        self.wall = wall
        #: Fixed centreline bend radius for the run's fittings. ``None`` derives it
        #: from the section size. Real ducting comes in straight sections + standard
        #: bends, so ``strict`` uses this radius exactly (see :attr:`strict`).
        self.bend_radius = bend_radius
        #: When True, emit only regular straight + fixed-radius bend segments and
        #: raise (naming the offending points) if the route is too tight to fit a
        #: real bend, instead of deforming the geometry to make it fit.
        self.strict = strict


class CableSystem(System):
    """Routed cable-tray carrier for signal services. Rendered as an open cable
    tray (a CHANNEL cross-section swept along the route), not a round pipe."""

    category: ClassVar[PortCategory] = "signal"

    def __init__(
        self,
        name: str,
        medium: str | None = None,
        metadata: dict | None = None,
        tray_width: float = 0.3,
        tray_height: float = 0.1,
        wall: float = 3e-3,
        bend_radius: float | None = None,
        strict: bool = False,
    ):
        super().__init__(name, medium=medium, metadata=metadata)
        self.tray_width = tray_width
        self.tray_height = tray_height
        self.wall = wall
        #: Fixed centreline bend radius for the tray's fittings; ``None`` derives it
        #: from the section size. See :attr:`strict`.
        self.bend_radius = bend_radius
        #: When True, emit only regular straight + fixed-radius bend segments and
        #: raise (naming the offending points) if the route is too tight to fit a
        #: real bend, instead of deforming the geometry to make it fit.
        self.strict = strict


class ElectricalSystem(CableSystem):
    """Cable system carrying electrical power at a given supply voltage. Shares
    the cable-tray geometry of :class:`CableSystem`."""

    category: ClassVar[PortCategory] = "electrical"

    def __init__(
        self,
        name: str,
        medium: str | None = None,
        metadata: dict | None = None,
        voltage: Voltage = Voltage.LV_400,
        tray_width: float = 0.3,
        tray_height: float = 0.1,
        wall: float = 3e-3,
        bend_radius: float | None = None,
        strict: bool = False,
    ):
        super().__init__(
            name,
            medium=medium,
            metadata=metadata,
            tray_width=tray_width,
            tray_height=tray_height,
            wall=wall,
            bend_radius=bend_radius,
            strict=strict,
        )
        self.voltage = voltage


# The built-in system kinds, keyed by the slug used in the procedural doc's
# ``type`` field and the DB system-template ``type``. Workers advertise these so
# the cellbuilder's system dropdown can union code-defined kinds with the
# per-scope DB system-template catalog.
SYSTEM_KINDS: dict[str, type[System]] = {
    "piping": PipingSystem,
    "duct": DuctSystem,
    "cable": CableSystem,
    "electrical": ElectricalSystem,
}


def list_system_types() -> list[str]:
    return list(SYSTEM_KINDS)


def system_type_specs() -> list[dict]:
    """Each built-in system kind as a catalog-shaped spec ``{slug, name,
    category, doc}`` (the ``doc`` mirrors ``ada.comms.rest.catalog`` system docs:
    type / medium / voltage / pipe knobs). Workers advertise these so the API can
    offer code kinds in the dropdown with their origin and "sync" one into the
    per-scope DB system-template catalog."""
    specs: list[dict] = []
    for slug, cls in SYSTEM_KINDS.items():
        probe = cls("_probe")
        specs.append(
            {
                "slug": slug,
                "name": slug.title(),
                "category": cls.category,
                "doc": {
                    "type": slug,
                    "medium": None,
                    "voltage": probe.voltage.value if isinstance(probe, ElectricalSystem) else None,
                    "pipe_radius": float(getattr(probe, "pipe_radius", 0.05)),
                    "pipe_wt": float(getattr(probe, "pipe_wt", 5e-3)),
                },
            }
        )
    return specs
