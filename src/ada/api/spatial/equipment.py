from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from .eq_types import EquipRepr
from .part import Part

if TYPE_CHECKING:
    from ada import LoadConceptCase, Point
    from ada.api.systems import Port, System


class Equipment(Part):
    def __init__(
        self,
        name: str,
        mass: float,
        cog: Iterable[float] | Point,
        origin: Iterable[float] | Point,
        lx: float,
        ly: float,
        lz: float,
        eq_repr: EquipRepr = EquipRepr.AS_IS,
        load_case_ref: str | LoadConceptCase = None,
        moment_equilibrium: bool = True,
        footprint: list[tuple[float, float]] = None,
        ports: list[Port] | None = None,
        ifc_element_class: str = "IfcBuildingElementProxy",
    ):
        from ada import Point

        super(Equipment, self).__init__(name=name)
        self.mass = mass
        self.cog = cog
        if not isinstance(origin, Point):
            origin = Point(*origin)
        self.origin = origin
        self.lx = lx
        self.ly = ly
        self.lz = lz
        self.eq_repr = eq_repr
        self.load_case_ref = load_case_ref
        self.moment_equilibrium = moment_equilibrium
        if footprint is None:
            lx_ = lx / 2
            ly_ = ly / 2
            footprint = [(-lx_, -ly_, lx_, ly_)]
        self.footprint = footprint
        # IFC element entity emitted for this equipment (e.g. "IfcPump", "IfcTank").
        # Distinct from Part.ifc_class, which picks the *spatial* entity type.
        self.ifc_element_class = ifc_element_class
        self.ports: list[Port] = []
        for port in ports if ports is not None else []:
            self.add_port(port)

    def add_port(self, port: Port) -> Port:
        """Attach a port to this equipment (sets ``port.parent``)."""
        if any(p.name == port.name for p in self.ports):
            raise ValueError(f"Equipment {self.name!r} already has a port named {port.name!r}")
        port.parent = self
        self.ports.append(port)
        return port

    def get_port(self, name: str) -> Port:
        for port in self.ports:
            if port.name == name:
                return port
        available = [p.name for p in self.ports]
        raise KeyError(f"Equipment {self.name!r} has no port {name!r}. Available ports: {available}")

    def connect(self, port_name: str, system: System) -> System:
        """Connect the named port to ``system`` (delegates to ``system.connect``)."""
        return system.connect(self, port_name)

    def unconnected_ports(self) -> list[Port]:
        return [p for p in self.ports if not p.is_connected]
