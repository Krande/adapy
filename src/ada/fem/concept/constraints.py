from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal

if TYPE_CHECKING:
    from ada import Point
    from ada.fem.concept.base import ConceptFEM


@dataclass
class ConstraintConcepts:
    parent_fem: ConceptFEM = None
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

    def get_global_constraint_concepts(self) -> ConstraintConcepts:
        all_parts = self.parent_fem.parent_part.get_all_parts_in_assembly(include_self=True)
        point_constraints = {}
        curve_constraints = {}

        for p in all_parts:
            for pc_name, pc in p.concept_fem.constraints.point_constraints.items():
                if pc_name in point_constraints:
                    raise ValueError(f"Point constraint with name {pc_name} already exists.")
                point_constraints[pc_name] = pc
            for cu_name, cu in p.concept_fem.constraints.curve_constraints.items():
                if cu_name in curve_constraints:
                    raise ValueError(f"Curve constraint with name {cu_name} already exists.")
                curve_constraints[cu_name] = cu

        return ConstraintConcepts(self.parent_fem, point_constraints, curve_constraints)


_all_dofs = {"dx", "dy", "dz", "rx", "ry", "rz"}


@dataclass
class ConstraintConceptDofType:
    dof: Literal["dx", "dy", "dz", "rx", "ry", "rz"]
    constraint_type: Literal["fixed", "free", "spring", "prescribed", "dependent", "super"]
    spring_stiffness: float = 0.0

    def __post_init__(self):
        if self.dof not in _all_dofs:
            raise ValueError(
                f"Invalid dof_type: {self.constraint_type}. Must be one of 'dx', 'dy', 'dz', 'rx', 'ry', 'rz'."
            )


def _constraint_dof_type_resolver(dof_constraints: list[ConstraintConceptDofType]) -> list[ConstraintConceptDofType]:
    user_dofs = {x.dof for x in dof_constraints}
    missing_dofs = _all_dofs.difference(user_dofs)
    for missing_dof in missing_dofs:
        dof_constraints.append(ConstraintConceptDofType(missing_dof, "fixed"))

    dof_map = {d.dof: d for d in dof_constraints}
    # sort self.dof_constraints in order of _all_dofs
    sorted_dofs = []
    for dof in _all_dofs:
        sorted_dofs.append(dof_map[dof])
    return sorted_dofs


@dataclass
class ConstraintConceptPoint:
    name: str
    position: Point | Iterable
    dof_constraints: list[ConstraintConceptDofType]

    def __post_init__(self):
        from ada import Point

        if not isinstance(self.position, Point):
            self.position = Point(*self.position)

        # fill in all dof_constraints not explicitly defined with "fixed
        self.dof_constraints = _constraint_dof_type_resolver(self.dof_constraints)


@dataclass
class ConstraintConceptCurve:
    name: str
    start_pos: Iterable | Point
    end_pos: Iterable | Point
    dof_constraints: list[ConstraintConceptDofType]

    def __post_init__(self):
        from ada import Point

        if not isinstance(self.start_pos, Point):
            self.start_pos = Point(*self.start_pos)

        if not isinstance(self.end_pos, Point):
            self.end_pos = Point(*self.end_pos)

        # fill in all dof_constraints not explicitly defined with "fixed
        self.dof_constraints = _constraint_dof_type_resolver(self.dof_constraints)
