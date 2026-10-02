from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal, TypeAlias, get_args

if TYPE_CHECKING:
    from ada import Beam, Point
    from ada.fem.concept.base import BeamConceptFEM, ConceptFEM


@dataclass
class ConstraintConcepts:
    parent_fem: ConceptFEM | BeamConceptFEM = None
    point_constraints: dict[str, ConstraintConceptPoint] = field(default_factory=dict)
    curve_constraints: dict[str, ConstraintConceptCurve] = field(default_factory=dict)
    rigid_links: dict[str, ConstraintConceptRigidLink] = field(default_factory=dict)
    beam_end_constraints: dict[str, ConstraintConceptBeamEnd] = field(default_factory=dict)

    def add_beam_end_constraint(self, constraint: ConstraintConceptBeamEnd) -> ConstraintConceptBeamEnd:
        if constraint.name in self.beam_end_constraints:
            raise ValueError(f"Beam end constraint with name {constraint.name} already exists.")

        self.beam_end_constraints[constraint.name] = constraint
        constraint.parent = self

        return constraint

    def add_point_constraint(self, constraint: ConstraintConceptPoint) -> ConstraintConceptPoint:
        if constraint.name in self.point_constraints:
            raise ValueError(f"Point constraint with name {constraint.name} already exists.")

        self.point_constraints[constraint.name] = constraint
        constraint.parent = self

        return constraint

    def add_curve_constraint(self, constraint: ConstraintConceptCurve) -> ConstraintConceptCurve:
        if constraint.name in self.curve_constraints:
            raise ValueError(f"Curve constraint with name {constraint.name} already exists.")

        self.curve_constraints[constraint.name] = constraint
        constraint.parent = self

        return constraint

    def add_rigid_link(self, constraint: ConstraintConceptRigidLink):
        if constraint.name in self.rigid_links:
            raise ValueError(f"Rigid link with name {constraint.name} already exists.")

        self.rigid_links[constraint.name] = constraint
        constraint.parent = self

        return constraint

    def get_global_constraint_concepts(self) -> ConstraintConcepts:
        """All constraint concepts in the assembly, including those assigned on beams"""
        return self._collect(self.parent_fem.parent_part.get_all_parts_in_assembly(include_self=True))

    def get_part_constraint_concepts(self) -> ConstraintConcepts:
        """All constraint concepts in the parent part and its sub-parts, including those assigned on beams"""
        return self._collect(self.parent_fem.parent_part.get_all_subparts(include_self=True))

    def _collect(self, parts) -> ConstraintConcepts:
        from ada import Beam

        collected = ConstraintConcepts(self.parent_fem)
        for p in parts:
            collected._merge(p.concept_fem.constraints)
            for bm in p.get_all_physical_objects(sub_elements_only=True, by_type=Beam):
                # avoid creating an empty concept container on every beam
                if bm.has_concept_fem:
                    collected._merge(bm.concept_fem.constraints)

        return collected

    def _merge(self, other: ConstraintConcepts) -> None:
        for attr in ("point_constraints", "curve_constraints", "rigid_links", "beam_end_constraints"):
            target: dict = getattr(self, attr)
            for name, constraint in getattr(other, attr).items():
                if name in target:
                    raise ValueError(f'Constraint with name "{name}" already exists in {attr}.')
                target[name] = constraint


_all_dofs = {"dx", "dy", "dz", "rx", "ry", "rz"}
# Define TypeAlias for DOF types
DofType: TypeAlias = Literal["dx", "dy", "dz", "rx", "ry", "rz"]

# Define TypeAlias for constraint types
ConstraintType: TypeAlias = Literal["fixed", "free", "spring", "prescribed", "dependent", "super"]


@dataclass
class ConstraintConceptDofType:
    dof: DofType
    constraint_type: ConstraintType
    spring_stiffness: float = 0.0

    def __post_init__(self):
        if self.dof not in _all_dofs:
            raise ValueError(
                f"Invalid dof_type: {self.constraint_type}. Must be one of 'dx', 'dy', 'dz', 'rx', 'ry', 'rz'."
            )

    @staticmethod
    def encastre(dof_type: ConstraintType = "fixed") -> list[ConstraintConceptDofType]:
        """All 6 dofs are fixed"""
        dofs = []
        for dof in _all_dofs:
            dofs.append(ConstraintConceptDofType(dof, dof_type))
        return dofs

    @staticmethod
    def pinned() -> list[ConstraintConceptDofType]:
        """All 3 translational dofs are fixed, and all 3 rotational dofs are free."""
        dofs = []
        for dof in _all_dofs:
            if dof == "rx" or dof == "ry" or dof == "rz":
                dofs.append(ConstraintConceptDofType(dof, "free"))
            else:
                dofs.append(ConstraintConceptDofType(dof, "fixed"))
        return dofs


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
    parent: ConstraintConcepts = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Point

        if not isinstance(self.position, Point):
            self.position = Point(*self.position)

        # fill in all dof_constraints not explicitly defined with "fixed
        self.dof_constraints = _constraint_dof_type_resolver(self.dof_constraints)


# Define TypeAlias for the ends of a beam
BeamEnd: TypeAlias = Literal["n1", "n2"]

#: How a beam-end support reaches the nodes of a shell/solid cross-section (a line mesh has one end node either way):
#:
#: - ``"coupled"``: a reference node on the beam axis carries the support and is tied to the section nodes by a
#:   kinematic coupling in all 6 dofs. The section moves as a rigid body about the beam end, so a pinned end is free
#:   to rotate.
#: - ``"direct"``: the support acts on every section node. Simpler -- no reference node, no coupling for the solver
#:   to support -- but a face whose nodes are all held in translation cannot rotate, so any end with its
#:   translations fixed is effectively clamped whatever its rotational dofs say. Solid-only nodes get dofs 1-3.
SectionSupport: TypeAlias = Literal["coupled", "direct"]


@dataclass
class ConstraintConceptBeamEnd:
    """A support at one end of a beam. In a shell/solid mesh it restrains the cross-section face of that end, through
    a coupled reference node or directly on the section nodes (see :data:`SectionSupport`)."""

    name: str
    beam: Beam
    end: BeamEnd
    dof_constraints: list[ConstraintConceptDofType]
    section_support: SectionSupport = "coupled"
    parent: ConstraintConcepts = field(init=False, repr=False)

    def __post_init__(self):
        if self.end not in get_args(BeamEnd):
            raise ValueError(f'Invalid beam end: "{self.end}". Must be one of {get_args(BeamEnd)}.')
        if self.section_support not in get_args(SectionSupport):
            raise ValueError(
                f'Invalid section support: "{self.section_support}". Must be one of {get_args(SectionSupport)}.'
            )

        # fill in all dof_constraints not explicitly defined with "fixed
        self.dof_constraints = _constraint_dof_type_resolver(self.dof_constraints)

    @property
    def position(self) -> Point:
        """The beam end position, in the coordinate system of the beam's parent part"""
        return getattr(self.beam, self.end).p


@dataclass
class ConstraintConceptCurve:
    name: str
    start_pos: Iterable | Point
    end_pos: Iterable | Point
    dof_constraints: list[ConstraintConceptDofType]
    parent: ConstraintConcepts = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Point

        if not isinstance(self.start_pos, Point):
            self.start_pos = Point(*self.start_pos)

        if not isinstance(self.end_pos, Point):
            self.end_pos = Point(*self.end_pos)

        # fill in all dof_constraints not explicitly defined with "fixed
        self.dof_constraints = _constraint_dof_type_resolver(self.dof_constraints)


@dataclass
class RigidLinkRegion:
    lower_corner: Iterable | Point
    upper_corner: Iterable | Point
    parent: ConstraintConcepts = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Point

        if not isinstance(self.lower_corner, Point):
            self.lower_corner = Point(*self.lower_corner)

        if not isinstance(self.upper_corner, Point):
            self.upper_corner = Point(*self.upper_corner)

    @staticmethod
    def from_center_and_offset(center: Iterable | Point, offset: Iterable | Point):
        from ada import Point

        if not isinstance(center, Point):
            center = Point(*center)

        if not isinstance(offset, Point):
            offset = Point(*offset)

        return RigidLinkRegion(center - offset, center + offset)


@dataclass
class ConstraintConceptRigidLink:
    name: str
    master_point: Iterable | Point
    influence_region: RigidLinkRegion
    dof_constraints: list[ConstraintConceptDofType]
    rotation_dependent: bool = True
    include_all_edges: bool = True
    parent: ConstraintConcepts = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Point

        if not isinstance(self.master_point, Point):
            self.master_point = Point(*self.master_point)
