"""Equipment as an analysis model places it: a box with a mass, set down in load cases.

GeniE models equipment as a *prism shape* (its dimensions, footprint, mass and centre of gravity,
all in the shape's own frame) and *places* it per load case: an origin and a local system, so the
same shape may stand in several load cases, and a wind "equipment" may stand on its side. At
meshing GeniE resolves every placement into line loads on the beams under the footprint (or into
masses), and its XML keeps that resolution as ``applied_loads`` beside the concepts.

These classes hold what was read and nothing more. They are not :class:`ada.Equipment` parts:
a part has one placement and one load case and no rotation, and a GeniE workspace routinely has
several of each. Nothing meshes or writes them; they are there to be drawn and weighed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

import numpy as np

if TYPE_CHECKING:
    from ada.fem.concept.base import ConceptFEM
    from ada.fem.concept.loads import LoadConceptLine, LoadConceptPoint

Vec3 = tuple[float, float, float]

#: The identity frame: a placement without a local system.
IDENTITY_AXES: tuple[Vec3, Vec3, Vec3] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


@dataclass
class EquipmentConceptPrism:
    """A box-shaped piece of equipment, in its own frame.

    ``footprint`` is the rectangles (``x1, y1, x2, y2``) it stands on, in the plane z=0 of that
    frame; the box rises from there to ``dimensions[2]``. ``mass`` is in kg.
    """

    name: str
    mass: float
    dimensions: Vec3
    cog: Vec3 = (0.0, 0.0, 0.0)
    footprint: list[tuple[float, float, float, float]] = field(default_factory=list)

    def local_box(self) -> tuple[Vec3, Vec3]:
        """The box's lower and upper corner in its own frame.

        The footprint gives the plan extent; without one the box is centred on the origin in
        plan, as GeniE draws a prism with no footprint of its own.
        """
        lx, ly, lz = self.dimensions
        if self.footprint:
            xs = [v for x1, _, x2, _ in self.footprint for v in (x1, x2)]
            ys = [v for _, y1, _, y2 in self.footprint for v in (y1, y2)]
            return (min(xs), min(ys), 0.0), (max(xs), max(ys), lz)
        return (-lx / 2, -ly / 2, 0.0), (lx / 2, ly / 2, lz)


@dataclass
class EquipmentConceptPlacement:
    """One prism set down in one load case: where its frame's origin is and how it is turned.

    ``axes`` are the frame's x, y and z directions in global coordinates.
    """

    equipment: str
    load_case: str
    origin: Vec3
    axes: tuple[Vec3, Vec3, Vec3] = IDENTITY_AXES
    moment_equilibrium: bool = True

    def to_global(self, local: Iterable[float]) -> Vec3:
        x, y, z = (np.asarray(a, dtype=float) for a in self.axes)
        lx, ly, lz = (float(v) for v in local)
        p = np.asarray(self.origin, dtype=float) + lx * x + ly * y + lz * z
        return float(p[0]), float(p[1]), float(p[2])


@dataclass
class EquipmentConcepts:
    """The prisms, their placements, and the loads the analysis model resolved them into.

    ``generated_loads`` is that resolution as read, by load case: ``(equipment name, load)``
    pairs. It is not part of :attr:`ConceptFEM.loads` -- those are the loads a user applied, and
    these are what the equipment already became; holding both as applied loads would count the
    equipment twice the moment anything meshed them.
    """

    parent_fem: ConceptFEM = None
    prisms: dict[str, EquipmentConceptPrism] = field(default_factory=dict)
    placements: list[EquipmentConceptPlacement] = field(default_factory=list)
    generated_loads: dict[str, list[tuple[str, LoadConceptLine | LoadConceptPoint]]] = field(default_factory=dict)

    def add_prism(self, prism: EquipmentConceptPrism) -> EquipmentConceptPrism:
        if prism.name in self.prisms:
            raise ValueError(f"Equipment {prism.name!r} already exists.")
        self.prisms[prism.name] = prism
        return prism

    def add_placement(self, placement: EquipmentConceptPlacement) -> EquipmentConceptPlacement:
        if placement.equipment not in self.prisms:
            raise ValueError(f"Placement of {placement.equipment!r}, which is not an equipment here.")
        self.placements.append(placement)
        return placement

    def add_generated_load(self, load_case: str, equipment: str, load) -> None:
        self.generated_loads.setdefault(load_case, []).append((equipment, load))

    def __bool__(self) -> bool:
        return bool(self.prisms)
