from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic
from ada.fem.concept.constraints import ConstraintConcepts
from ada.fem.concept.loads import LoadConcepts

if TYPE_CHECKING:
    from ada import Part


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
