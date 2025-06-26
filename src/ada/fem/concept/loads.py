from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal

if TYPE_CHECKING:
    from ada import Direction, Plate, Point
    from ada.fem.concept.base import ConceptFEM


class DesignCondition(str, enum.Enum):
    OPERATING = "operating"

    @staticmethod
    def from_string(value: str) -> "DesignCondition":
        if value.lower() == "operating":
            return DesignCondition.OPERATING
        raise ValueError(f"Unknown design condition: {value}")


@dataclass
class LoadConcepts:
    parent_fem: ConceptFEM = None
    load_cases: dict[str, LoadConceptCase] = field(default_factory=dict)
    load_case_combinations: dict[str, LoadConceptCaseCombination] = field(default_factory=dict)

    def add_load_case(self, load_case: LoadConceptCase) -> LoadConceptCase:
        if load_case.name in self.load_cases:
            raise ValueError(f"Load case with name {load_case.name} already exists.")
        self.load_cases[load_case.name] = load_case
        load_case.parent = self
        return load_case

    def add_load_case_combination(
        self, load_case_combination: LoadConceptCaseCombination
    ) -> LoadConceptCaseCombination:
        if load_case_combination.name in self.load_case_combinations:
            raise ValueError(f"Load case combination with name {load_case_combination.name} already exists.")
        self.load_case_combinations[load_case_combination.name] = load_case_combination
        return load_case_combination

    def get_global_load_concepts(self) -> LoadConcepts:
        """Consolidate all load cases and load combinations from all parts into a new LoadConcepts object."""
        load_cases = {}
        load_combinations = {}
        all_parts = self.parent_fem.parent_part.get_all_parts_in_assembly(include_self=True)
        for p in all_parts:
            for lc_name, lc in p.concept_fem.loads.load_cases.items():
                if lc_name not in load_cases:
                    load_cases[lc_name] = lc
                else:
                    # Merge loads if they have the same name
                    load_cases[lc_name].loads.extend(lc.loads)
            for lcc_name, lcc in p.concept_fem.loads.load_case_combinations.items():
                if lcc_name not in load_combinations:
                    load_combinations[lcc_name] = lcc
                else:
                    # Merge load cases in combinations if they have the same name
                    existing_lcc = load_combinations[lcc_name]
                    existing_lcc.load_cases.extend(lcc.load_cases)

        return LoadConcepts(self.parent_fem, load_cases, load_combinations)


@dataclass
class LoadConceptPoint:
    name: str
    position: Point | Iterable
    force: tuple[float, float, float]
    moment: tuple[float, float, float]
    system: Literal["local", "global"] = "local"
    parent: LoadConceptCase = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Point

        if isinstance(self.position, Iterable):
            self.position = Point(*self.position)

        if not isinstance(self.position, Point):
            raise TypeError("point must be of type Point or convertible to Point.")


@dataclass
class LoadConceptLine:
    name: str
    start_point: Point | Iterable
    end_point: Point | Iterable
    intensity_start: tuple[float, float, float]
    intensity_end: tuple[float, float, float]
    system: Literal["local", "global"] = "local"
    parent: LoadConceptCase = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Point

        if isinstance(self.start_point, Iterable):
            self.start_point = Point(*self.start_point)
        if isinstance(self.end_point, Iterable):
            self.end_point = Point(*self.end_point)

        if not isinstance(self.start_point, Point) or not isinstance(self.end_point, Point):
            raise TypeError("start_point and end_point must be of type Point or convertible to Point.")


@dataclass
class LoadConceptSurface:
    name: str
    plate_ref: Plate = None
    points: list[Iterable] = None
    pressure: float = None
    side: Literal["front", "back"] = "front"
    system: Literal["local", "global"] = "local"
    parent: LoadConceptCase = field(init=False, repr=False)

    def __post_init__(self):
        if self.plate_ref is None and self.points is None:
            raise ValueError("Either plate_ref or points must be provided for LoadConceptSurface.")


@dataclass
class RotationalAccelerationField:
    rotational_point: tuple[float, float, float] | Point
    rotational_axis: tuple[float, float, float] | Direction
    angular_acceleration: float
    angular_velocity: float
    parent: LoadConceptAccelerationField = field(init=False, repr=False)

    def __post_init__(self):
        from ada import Direction, Point

        if not isinstance(self.rotational_point, Point):
            self.rotational_point = Point(*self.rotational_point)
        if not isinstance(self.rotational_axis, Direction):
            self.rotational_axis = Direction(*self.rotational_axis)


@dataclass
class LoadConceptAccelerationField:
    name: str
    acceleration: tuple[float, float, float]
    include_self_weight: bool = True
    rotational_field: RotationalAccelerationField = None
    parent: LoadConceptCase = field(init=False, repr=False)

    def __post_init__(self):
        if self.rotational_field is not None:
            self.rotational_field.parent = self


@dataclass
class LoadConceptCase:
    name: str
    loads: list[LoadConceptLine | LoadConceptPoint | LoadConceptSurface | LoadConceptAccelerationField] = field(
        default_factory=list
    )
    design_condition: DesignCondition = DesignCondition.OPERATING
    fem_loadcase_number: int = 1
    complex_type: Literal["static"] = "static"
    invalidated: bool = True
    include_self_weight: bool = False
    mesh_loads_as_mass: bool = False
    parent: LoadConcepts = field(init=False, repr=False)

    def __post_init__(self):
        for load in self.loads:
            load.parent = self
            if isinstance(load, LoadConceptAccelerationField):
                if load.include_self_weight:
                    self.include_self_weight = True


@dataclass
class LoadConceptCaseFactored:
    load_case: LoadConceptCase
    factor: float
    phase: int = 0


@dataclass
class LoadConceptCaseCombination:
    name: str
    load_cases: list[LoadConceptCaseFactored]
    design_condition: DesignCondition | Literal["operating"] = DesignCondition.OPERATING
    complex_type: Literal["static"] = "static"
    invalidated: bool = True
    convert_load_to_mass: bool = False
    global_scale_factor: float = 1.0
    equipments_type: Literal["line_load"] = "line_load"
