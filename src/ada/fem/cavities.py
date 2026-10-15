"""Gas-filled closed cavities: a volume of ideal gas bounded by a closed surface of the mesh.

A :class:`FluidCavity` couples the pressure inside a closed surface to the volume that surface encloses -- an
air-supported structure, a pressurised membrane cushion, an inflated tube. The gas is an ideal gas
(:class:`IdealGas`): ``p V = n R T`` with ``n = m / M``, and with no gas let in or out the mass is fixed by the
initial state.

Solvers that have the construct write it from this object (Abaqus ``*FLUID CAVITY``, OpenCourant / OpenRadioss
``/MONVOL/GAS``); the ones that do not (Code_Aster, CalculiX, Sesam, Usfos) refuse a model that holds one rather
than solving it without the gas.

:func:`enclosed_volume` and the :class:`IdealGas` methods answer the same questions without a solver: the volume a
surface encloses, and the pressure of the gas at another volume and temperature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Union

import numpy as np

from .common import FemBase
from .sets import FemSet
from .surfaces import Surface

if TYPE_CHECKING:
    from ada import FEM, Assembly, Node

#: The molar gas constant, J/(mol K) (CODATA 2018, exact).
UNIVERSAL_GAS_CONSTANT = 8.314462618


@dataclass(frozen=True)
class IdealGas:
    """An ideal gas at its initial state.

    :param molecular_weight: molar mass, kg/mol (air 0.028964).
    :param initial_pressure: absolute pressure at the start of the analysis, Pa.
    :param initial_temperature: absolute temperature at the start of the analysis, K.
    :param ambient_pressure: absolute pressure outside the cavity, Pa. The pressure the cavity's walls carry is the
        difference, ``pressure - ambient_pressure``.
    :param gamma: ratio of specific heats ``cp / cv`` (1.4 for a diatomic gas such as air). Used by an adiabatic
        cavity, and for the gas's heat capacity where a solver asks for one.
    :param adiabatic: whether the gas is compressed adiabatically (``p V^gamma`` constant) rather than at the
        temperature it is given (isothermally, by default).
    """

    molecular_weight: float
    initial_pressure: float
    initial_temperature: float
    ambient_pressure: float = 0.0
    gamma: float = 1.4
    adiabatic: bool = False

    def __post_init__(self):
        if self.molecular_weight <= 0.0:
            raise ValueError("molecular_weight must be positive (kg/mol)")
        if self.initial_pressure <= 0.0:
            raise ValueError("initial_pressure is absolute and must be positive (Pa)")
        if self.initial_temperature <= 0.0:
            raise ValueError("initial_temperature is absolute and must be positive (K)")
        if self.ambient_pressure < 0.0:
            raise ValueError("ambient_pressure is absolute and cannot be negative (Pa)")
        if self.gamma <= 1.0:
            raise ValueError("gamma (cp / cv) must be greater than 1")

    @property
    def specific_gas_constant(self) -> float:
        """``R / M``, J/(kg K)."""
        return UNIVERSAL_GAS_CONSTANT / self.molecular_weight

    @property
    def molar_cp(self) -> float:
        """Molar heat capacity at constant pressure, J/(mol K): ``gamma R / (gamma - 1)``."""
        return self.gamma * UNIVERSAL_GAS_CONSTANT / (self.gamma - 1.0)

    @property
    def initial_density(self) -> float:
        """kg/m^3."""
        return self.initial_pressure / (self.specific_gas_constant * self.initial_temperature)

    @property
    def initial_gauge_pressure(self) -> float:
        """The initial pressure relative to the ambient pressure, Pa."""
        return self.initial_pressure - self.ambient_pressure

    def mass(self, initial_volume: float) -> float:
        """The mass of gas in a cavity of ``initial_volume`` at the initial state, kg."""
        return self.initial_density * initial_volume

    def pressure(self, volume: float, initial_volume: float, temperature: float | None = None) -> float:
        """The absolute pressure of the cavity's gas (a fixed mass) at ``volume``, Pa.

        Adiabatic gas: ``p0 (V0 / V)^gamma``, and ``temperature`` must be None (it follows from the volume).
        Otherwise ``p0 (V0 / V) (T / T0)``, at the initial temperature when ``temperature`` is None.
        """
        if volume <= 0.0 or initial_volume <= 0.0:
            raise ValueError("volumes must be positive")
        if self.adiabatic:
            if temperature is not None:
                raise ValueError("an adiabatic gas's temperature follows from its volume; pass no temperature")
            return self.initial_pressure * (initial_volume / volume) ** self.gamma
        t = self.initial_temperature if temperature is None else temperature
        return self.initial_pressure * (initial_volume / volume) * (t / self.initial_temperature)

    def temperature(self, volume: float, initial_volume: float) -> float:
        """The temperature of an adiabatic gas at ``volume``, K: ``T0 (V0 / V)^(gamma - 1)``; an isothermal gas keeps
        its initial temperature."""
        if not self.adiabatic:
            return self.initial_temperature
        return self.initial_temperature * (initial_volume / volume) ** (self.gamma - 1.0)


#: What a cavity is bounded by: an element-based ``Surface``, or an element set of shells (or membranes).
CavityRegion = Union[Surface, FemSet]


class FluidCavity(FemBase):
    """A closed surface of the mesh filled with gas.

    :param name: unique name of the cavity.
    :param surface: the closed surface bounding the gas -- an element-based :class:`~ada.fem.Surface`, or a
        :class:`~ada.fem.FemSet` of shell or membrane elements. Its facets must be oriented consistently (all normals
        out of the cavity, or all into it); :func:`enclosed_volume` checks that it is closed.
    :param ref_node: the cavity's reference node, which carries the cavity pressure in solvers that have one (Abaqus).
        It need not lie on the surface; a node no element uses is the usual choice.
    :param gas: the gas and its initial state.
    :param additional_volume: volume of gas outside the meshed surface that is connected to it (piping, a
        reservoir), m^3.
    """

    def __init__(
        self,
        name: str,
        surface: CavityRegion,
        ref_node: Node,
        gas: IdealGas,
        additional_volume: float = 0.0,
        metadata: dict | None = None,
        parent: FEM | None = None,
    ):
        super().__init__(name, metadata, parent)
        if isinstance(surface, FemSet) and surface.type != FemSet.TYPES.ELSET:
            raise ValueError(f"FluidCavity {name!r}: a cavity is bounded by elements; {surface.name!r} is a node set")
        if isinstance(surface, Surface) and surface.type != Surface.TYPES.ELEMENT:
            raise ValueError(f"FluidCavity {name!r}: a cavity is bounded by an element-based surface")
        if additional_volume < 0.0:
            raise ValueError(f"FluidCavity {name!r}: additional_volume cannot be negative")
        self._surface = surface
        self._ref_node = ref_node
        self._gas = gas
        self._additional_volume = float(additional_volume)

    @property
    def surface(self) -> CavityRegion:
        return self._surface

    @property
    def ref_node(self) -> Node:
        return self._ref_node

    @property
    def gas(self) -> IdealGas:
        return self._gas

    @property
    def additional_volume(self) -> float:
        return self._additional_volume

    def volume(self) -> float:
        """The volume the cavity holds in the undeformed mesh: what the surface encloses plus ``additional_volume``."""
        return enclosed_volume(self.surface) + self.additional_volume

    def shell_elements(self) -> list:
        """The shell (or membrane) elements the surface is made of, in surface order, each once."""
        from .shapes.definitions import ShellShapes
        from .surfaces import surface_facets

        out, seen = [], set()
        for facet in surface_facets(self.surface):
            el = facet.element
            if not isinstance(el.type, ShellShapes):
                raise ValueError(f"FluidCavity {self.name!r}: element {el.id} ({el.type}) is not a shell")
            if el.id not in seen:
                seen.add(el.id)
                out.append(el)
        return out

    def __repr__(self):
        surf = getattr(self.surface, "name", self.surface)
        return f'FluidCavity("{self.name}", surface="{surf}", ref_node={self.ref_node.id})'


# ---- geometry ----------------------------------------------------------------------------------------------------


def oriented_facets(region: CavityRegion) -> list[np.ndarray]:
    """The corner coordinates of each facet of ``region``, wound so that the facet normal is the surface's.

    A shell facet named by its negative face (``SNEG``, ``-1``) is reversed. Mid-side and centre nodes are left out:
    the facets are taken as flat triangles and bilinear quadrilaterals between their corners.
    """
    from .shapes.definitions import ShellShapes
    from .surfaces import surface_facets

    out = []
    for facet in surface_facets(region):
        if not isinstance(facet.shape, ShellShapes):
            raise ValueError(
                f"facet of element {facet.element.id} is an edge ({facet.shape}); a cavity surface is made of faces"
            )
        n_corners = 3 if facet.shape in (ShellShapes.TRI, ShellShapes.TRI6, ShellShapes.TRI7) else 4
        pts = facet.points[:n_corners]
        if _is_reversed(facet):
            pts = pts[::-1]
        out.append(pts)
    return out


def _is_reversed(facet) -> bool:
    """Whether a facet's node order winds against the surface's normal: a shell's negative face, and every solid
    face -- the Abaqus face tables (``S1`` of a C3D8 is nodes 1-2-3-4) wind a face with its right-hand normal into the
    element, and a solid's surface faces out of it."""
    from .shapes.definitions import SolidShapes

    if isinstance(facet.element.type, SolidShapes):
        return True
    return _is_negative_side(facet.side)


def _is_negative_side(side) -> bool:
    if isinstance(side, str):
        return side.strip().upper() == "SNEG"
    return isinstance(side, (int, np.integer)) and int(side) == -1


def signed_volume(facets: Iterable[np.ndarray]) -> float:
    """The volume a closed surface of triangles and quadrilaterals encloses, by the divergence theorem:
    ``V = 1/3 \\oint x . n dA``. Positive when the facets' normals point out of the volume, negative when in.

    A triangle contributes ``p0 . (p1 x p2) / 6``; a quadrilateral, taken as the bilinear surface between its corners,
    ``(p0 + p1 + p2 + p3) . ((p2 - p0) x (p3 - p1)) / 24``, which is exact for a warped quadrilateral too.
    """
    total = 0.0
    for pts in facets:
        if len(pts) == 3:
            total += float(np.dot(pts[0], np.cross(pts[1], pts[2]))) / 6.0
        elif len(pts) == 4:
            total += float(np.dot(pts.sum(axis=0), np.cross(pts[2] - pts[0], pts[3] - pts[1]))) / 24.0
        else:
            raise ValueError(f"a facet of {len(pts)} corners is neither a triangle nor a quadrilateral")
    return total


def check_closed(region: CavityRegion) -> None:
    """Raise unless ``region`` is a closed, consistently oriented surface: every facet edge is shared by exactly two
    facets, which run along it in opposite directions."""
    from .shapes.definitions import ShellShapes
    from .surfaces import surface_facets

    edges: dict[tuple[int, int], int] = {}
    for facet in surface_facets(region):
        n_corners = 3 if facet.shape in (ShellShapes.TRI, ShellShapes.TRI6, ShellShapes.TRI7) else 4
        ids = list(facet.node_ids[:n_corners])
        if _is_reversed(facet):
            ids = ids[::-1]
        for a, b in zip(ids, ids[1:] + ids[:1]):
            edges[(a, b)] = edges.get((a, b), 0) + 1
    name = getattr(region, "name", "")
    for (a, b), count in edges.items():
        if count > 1:
            raise ValueError(
                f"surface {name!r}: edge {a}-{b} is run in the same direction by {count} facets -- the facets are not "
                f"oriented consistently (or more than two facets meet at the edge)"
            )
        if (b, a) not in edges:
            raise ValueError(f"surface {name!r} is not closed: edge {a}-{b} belongs to one facet only")


def enclosed_volume(region: CavityRegion, check: bool = True) -> float:
    """The volume a closed surface encloses, m^3 (positive whichever way its normals point).

    ``check`` first verifies the surface is closed and consistently oriented (:func:`check_closed`); without that the
    divergence theorem's sum is not a volume.
    """
    if check:
        check_closed(region)
    return abs(signed_volume(oriented_facets(region)))


def refuse_fluid_cavities(assembly: Assembly, writer: str) -> None:
    """Raise :class:`~ada.fem.exceptions.model_definition.UnsupportedFeature` when the model holds a fluid cavity:
    ``writer`` has no gas-filled cavity, and solving the model without the gas is a different structure."""
    from .exceptions.model_definition import UnsupportedFeature

    names = [
        name for p in assembly.get_all_parts_in_assembly(include_self=True) for name in p.fem.fluid_cavities.keys()
    ]
    if names:
        raise UnsupportedFeature(
            f"{writer} writer: {writer} has no gas-filled cavity, and the model holds {', '.join(map(repr, names))}. "
            "Write it to Abaqus or OpenCourant, or replace the gas with a pressure load."
        )
