from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Point


class DesignCondition(str, enum.Enum):
    OPERATING = "operating"


    @staticmethod
    def from_string(value: str) -> "DesignCondition":
        if value.lower() == "operating":
            return DesignCondition.OPERATING
        raise ValueError(f"Unknown design condition: {value}")

@dataclass
class LoadConceptLine:
    name: str

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
