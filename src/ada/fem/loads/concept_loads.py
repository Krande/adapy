from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ada import Part, Point


class DesignCondition(str, enum.Enum):
    OPERATING = "operating"

    @staticmethod
    def from_string(value: str) -> "DesignCondition":
        if value.lower() == "operating":
            return DesignCondition.OPERATING
        raise ValueError(f"Unknown design condition: {value}")


@dataclass
class LoadConcepts:
    parent_part: Part
    load_cases: dict[str, LoadCase] = field(default_factory=dict)
    load_case_combinations: dict[str, LoadCaseCombination] = field(default_factory=dict)

    def add_load_case(self, load_case: LoadCase) -> LoadCase:
        if load_case.name in self.load_cases:
            raise ValueError(f"Load case with name {load_case.name} already exists.")
        self.load_cases[load_case.name] = load_case
        return load_case

    def add_load_case_combination(self, load_case_combination: LoadCaseCombination) -> LoadCaseCombination:
        if load_case_combination.name in self.load_case_combinations:
            raise ValueError(f"Load case combination with name {load_case_combination.name} already exists.")
        self.load_case_combinations[load_case_combination.name] = load_case_combination
        return load_case_combination

    def get_global_load_concepts(self) -> LoadConcepts:
        """Consolidate all load cases and load combinations from all parts into a new LoadConcepts object."""
        load_cases = {}
        load_combinations = {}
        all_parts = self.parent_part.get_all_parts_in_assembly(include_self=True)
        for p in all_parts:
            for lc_name, lc in p.load_concepts.load_cases.items():
                if lc_name not in load_cases:
                    load_cases[lc_name] = lc
                else:
                    # Merge loads if they have the same name
                    load_cases[lc_name].loads.extend(lc.loads)
            for lcc_name, lcc in p.load_concepts.load_case_combinations.items():
                if lcc_name not in load_combinations:
                    load_combinations[lcc_name] = lcc
                else:
                    # Merge load cases in combinations if they have the same name
                    existing_lcc = load_combinations[lcc_name]
                    existing_lcc.load_cases.extend(lcc.load_cases)

        return LoadConcepts(self.parent_part, load_cases, load_combinations)


@dataclass
class LoadConceptLine:
    name: str
    start_point: Point
    end_point: Point
    intensity_start: tuple[float, float, float]
    intensity_end: tuple[float, float, float]
    system: Literal["local", "global"] = "local"


@dataclass
class LoadConceptPoint:
    name: str
    point: Point


@dataclass
class LoadCase:
    name: str
    loads: list[LoadConceptLine]
    design_condition: DesignCondition
    fem_loadcase_number: int
    complex_type: Literal["static"]
    invalidated: bool = True


@dataclass
class LoadCaseFactored:
    load_case: LoadCase
    factor: float
    phase: int = 0


@dataclass
class LoadCaseCombination:
    name: str
    load_cases: list[LoadCaseFactored]
    design_condition: DesignCondition
    complex_type: Literal["static"]
    invalidated: bool = True
    convert_load_to_mass: bool = False
    global_scale_factor: float = 1.0
    equipments_type: Literal["line_load"] = "line_load"
