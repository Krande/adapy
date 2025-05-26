from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Iterable

if TYPE_CHECKING:
    from ada import Part, Point, Plate



@dataclass
class ConstraintConcepts:
    parent_part: Part
    point_constraints: dict[str, ConstraintConceptPoint] = field(default_factory=dict)
    curve_constraints: dict[str, ConstraintConceptCurve] = field(default_factory=dict)

    def add_point_constraint(self, constraint: ConstraintConceptPoint) -> ConstraintConceptPoint:
        if constraint.name in self.point_constraints:
            raise ValueError(f"Point constraint with name {constraint.name} already exists.")
        self.point_constraints[constraint.name] = constraint
        return constraint

    def add_curve_constraint(self, constraint: ConstraintConceptCurve) -> ConstraintConceptCurve:
        if constraint.name in self.curve_constraints:
            raise ValueError(f"Curve constraint with name {constraint.name} already exists.")
        self.curve_constraints[constraint.name] = constraint
        return constraint


@dataclass
class ConstraintConceptDofType:
    dof: Literal["x", "y", "z", "rx", "ry", "rz"]
    dof_type: Literal["fixed", "free", "spring", "prescribed", "dependent", "super"]
    spring_stiffness: float = 0.0

    def __post_init__(self):
        if self.dof_type not in ["x", "y", "z", "rx", "ry", "rz"]:
            raise ValueError(f"Invalid dof_type: {self.dof_type}. Must be one of 'x', 'y', 'z', 'rx', 'ry', 'rz'.")

@dataclass
class ConstraintConceptPoint:
    name: str
    point: Point | Iterable
    dof_constraints: list[ConstraintConceptDofType]


@dataclass
class ConstraintConceptCurve:
    name: str
    start_pos: Iterable | Point
    end_pos: Iterable | Point
    dof_constraints: list[ConstraintConceptDofType]