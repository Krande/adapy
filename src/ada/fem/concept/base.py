from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic
from ada.fem.concept.constraints import (
    BeamEnd,
    ConstraintConceptBeamEnd,
    ConstraintConceptDofType,
    ConstraintConcepts,
    SectionSupport,
)
from ada.fem.concept.loads import LoadConcepts

if TYPE_CHECKING:
    from ada import Beam, Part


@dataclass
class ConceptFEM:
    """
    ConceptFEM represents all FEM related properties defined on the conceptual level (Part/assembly/Beam/Plate etc).

    While the FEM object represents a self-contained FE model with mesh generated from the conceptual level
    """

    parent_part: Part
    loads: LoadConcepts = field(default_factory=LoadConcepts)
    constraints: ConstraintConcepts = field(default_factory=ConstraintConcepts)
    steps: dict[str, StepImplicitStatic] = field(default_factory=dict)

    def add_step(self, step: StepImplicitStatic) -> StepImplicitStatic:
        if step.name in self.steps:
            raise ValueError(f"step name {step.name} already exists")

        self.steps[step.name] = step

    def __post_init__(self):
        self.loads.parent_fem = self
        self.constraints.parent_fem = self


@dataclass
class BeamConceptFEM:
    """FEM related properties defined on a beam. They follow the beam and take precedence over part level concepts"""

    beam: Beam
    constraints: ConstraintConcepts = field(default_factory=ConstraintConcepts)

    def __post_init__(self):
        self.constraints.parent_fem = self

    @property
    def parent_part(self) -> Part | None:
        return self.beam.parent

    def add_end_constraint(
        self,
        end: BeamEnd,
        dof_constraints: list[ConstraintConceptDofType],
        name: str = None,
        section_support: SectionSupport = "coupled",
    ) -> ConstraintConceptBeamEnd:
        """Add a support at the beam end. Dofs not given in dof_constraints are fixed.

        ``section_support`` chooses how a shell/solid mesh's end section is supported: through a reference node
        coupled to the section (``"coupled"``, the default) or on the section nodes themselves (``"direct"``). See
        :data:`ada.fem.concept.constraints.SectionSupport`.
        """
        name = f"{self.beam.name}_{end}" if name is None else name
        return self.constraints.add_beam_end_constraint(
            ConstraintConceptBeamEnd(name, self.beam, end, dof_constraints, section_support=section_support)
        )

    def fix_end(
        self, end: BeamEnd, name: str = None, section_support: SectionSupport = "coupled"
    ) -> ConstraintConceptBeamEnd:
        """All 6 dofs at the beam end are fixed"""
        return self.add_end_constraint(end, ConstraintConceptDofType.encastre(), name, section_support)

    def pin_end(
        self, end: BeamEnd, name: str = None, section_support: SectionSupport = "coupled"
    ) -> ConstraintConceptBeamEnd:
        """The 3 translational dofs at the beam end are fixed, and the 3 rotational dofs are free.

        Use the default ``"coupled"`` support on a shell/solid mesh: held in translation on every section node
        (``"direct"``), the end cannot rotate and is clamped.
        """
        return self.add_end_constraint(end, ConstraintConceptDofType.pinned(), name, section_support)
