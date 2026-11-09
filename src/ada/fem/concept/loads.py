from __future__ import annotations

import copy
import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal

if TYPE_CHECKING:
    from ada import Direction, Plate, Point
    from ada.fem.concept.base import ConceptFEM
    from ada.fem.concept.constraints import ConstraintConceptPoint


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
        """Consolidate all load cases and load combinations from all parts into a new LoadConcepts object.

        Same-named load cases of several parts become one case holding the loads of each, and same-named
        combinations one combination of the terms of each. Nothing of the model is changed: a merged case is a new
        object, and a case or combination (or a load or term) that two parts hold is taken once. The merge used to
        extend the first part's case in place, so each call added the other parts' loads to the model again, and a
        case two parts held was extended by its own loads: written twice to the GeniE XML, three times on the next
        call.
        """
        load_cases: dict[str, LoadConceptCase] = {}
        load_combinations: dict[str, LoadConceptCaseCombination] = {}
        merged: set[str] = set()
        merged_combinations: set[str] = set()
        all_parts = self.parent_fem.parent_part.get_all_parts_in_assembly(include_self=True)
        for p in all_parts:
            for lc_name, lc in p.concept_fem.loads.load_cases.items():
                first = load_cases.get(lc_name)
                if first is None:
                    load_cases[lc_name] = lc
                elif first is not lc:
                    if lc_name not in merged:
                        # a shallow copy keeps the first case's attributes and parent, and the loads keep theirs
                        # (each load is placed by the part of the case it was made in)
                        first = copy.copy(first)
                        first.loads = list(first.loads)
                        load_cases[lc_name] = first
                        merged.add(lc_name)
                    _extend_once(first.loads, lc.loads)
            for lcc_name, lcc in p.concept_fem.loads.load_case_combinations.items():
                first = load_combinations.get(lcc_name)
                if first is None:
                    load_combinations[lcc_name] = lcc
                elif first is not lcc:
                    if lcc_name not in merged_combinations:
                        first = copy.copy(first)
                        first.load_cases = list(first.load_cases)
                        load_combinations[lcc_name] = first
                        merged_combinations.add(lcc_name)
                    _extend_once(first.load_cases, lcc.load_cases)

        return LoadConcepts(self.parent_fem, load_cases, load_combinations)


def _extend_once(target: list, items: Iterable) -> None:
    """Append to ``target`` each of ``items`` it does not already hold (by identity)."""
    held = {id(x) for x in target}
    for x in items:
        if id(x) not in held:
            held.add(id(x))
            target.append(x)


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
    """A normal pressure on a plate (``plate_ref``) or over a polygon footprint (``points``).

    ``pressure_gradient`` (per unit length, in the load's position axes) makes the pressure vary linearly:
    ``p(x) = pressure + pressure_gradient . x``, so ``pressure`` is then its value at the origin of those axes.
    GeniE's ``pressure2d_linear_function`` is exactly that (k1..k3 the gradient, k4 the constant); its
    ``pressure2d_3point_varying`` is read as the linear field through its three values. ``None``: uniform.
    """

    name: str
    plate_ref: Plate = None
    points: list[Iterable] = None
    pressure: float = None
    side: Literal["front", "back"] = "front"
    system: Literal["local", "global"] = "local"
    pressure_gradient: tuple[float, float, float] | None = None
    #: A traction instead of a normal pressure: a force per unit area by components (fx, fy, fz) in the load's
    #: axes (GeniE's ``component2d_constant``). ``pressure`` is then not used.
    traction: tuple[float, float, float] | None = None
    parent: LoadConceptCase = field(init=False, repr=False)

    def __post_init__(self):
        if self.plate_ref is None and self.points is None:
            raise ValueError("Either plate_ref or points must be provided for LoadConceptSurface.")
        if self.traction is not None:
            t = tuple(float(c) for c in self.traction)
            if len(t) != 3:
                raise ValueError("traction must have three components")
            self.traction = t
        if self.pressure_gradient is not None:
            g = tuple(float(c) for c in self.pressure_gradient)
            if len(g) != 3:
                raise ValueError("pressure_gradient must have three components")
            self.pressure_gradient = None if g == (0.0, 0.0, 0.0) else g

    def pressure_at(self, position) -> float:
        """The pressure at ``position`` (in the load's position axes)."""
        if self.pressure_gradient is None:
            return float(self.pressure)
        return float(self.pressure) + float(sum(g * float(c) for g, c in zip(self.pressure_gradient, position)))


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
class LoadConceptPrescribedDisplacement:
    """The displacement of a support point in one load case, on the dofs that support declares ``"prescribed"``.

    GeniE's ``PrescribedDisplacement(LC, Sp, dx, dy, dz, rx, ry, rz)``. A prescribed displacement is loading in
    Sesam, so it belongs to a load case and one support can carry a different one in each: GeniE V8.13 meshed a
    support with prescribed dx, dz and rz and a displacement in two load cases into BNBCD code 2 on dofs 1, 3, 6 and
    one BNDISPL per load case (-0.003 in LC1; 0.005, -0.01, 0.001 in LC2). The values are in the global axes of the
    support (a support in a rotated local system is not read with a prescribed dof) and every dof is kept as
    written, including those the support does not prescribe.
    """

    name: str
    support: ConstraintConceptPoint
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    parent: LoadConceptCase = field(init=False, repr=False)


@dataclass
class LoadConceptCase:
    name: str
    loads: list[
        LoadConceptLine
        | LoadConceptPoint
        | LoadConceptSurface
        | LoadConceptAccelerationField
        | LoadConceptPrescribedDisplacement
    ] = field(default_factory=list)
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
