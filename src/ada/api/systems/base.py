"""Systems: logical service networks (piping, ducting, cabling, electrical).

A ``System`` is not a :class:`ada.Part` — it is the logical object that
equipment ports connect to. Wiring reads fluently::

    cooling = PipingSystem("CoolingWater", medium="water").connect(pump, "discharge").connect(tank, "inlet")

Routing the system through a :class:`~ada.topology.grid.CellGrid` (and turning
the routed path into geometry) is delegated to ``ada.topology.routing`` via the
:meth:`System.route` convenience.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from .categories import PortCategory, Voltage
from .ports import Port

if TYPE_CHECKING:
    from ada.api.spatial.equipment import Equipment
    from ada.geom.points import Point
    from ada.topology.grid import CellGrid
    from ada.topology.routing import RoutingRules

__all__ = ["System", "PipingSystem", "DuctSystem", "CableSystem", "ElectricalSystem"]


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
    category: ClassVar[PortCategory] = "process"


class CableSystem(System):
    """Routed cable/tray carrier for signal services."""

    category: ClassVar[PortCategory] = "signal"


class ElectricalSystem(CableSystem):
    """Cable system carrying electrical power at a given supply voltage."""

    category: ClassVar[PortCategory] = "electrical"

    def __init__(
        self,
        name: str,
        medium: str | None = None,
        metadata: dict | None = None,
        voltage: Voltage = Voltage.LV_400,
    ):
        super().__init__(name, medium=medium, metadata=metadata)
        self.voltage = voltage
