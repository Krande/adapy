from __future__ import annotations

from enum import Enum

from ada.config import Config


class InvalidUnit(Exception):
    pass


class Units(Enum):
    M = "m"
    MM = "mm"

    @staticmethod
    def is_valid_unit(unit: str):
        return unit.lower() in list([x.value.lower() for x in Units])

    @staticmethod
    def from_str(unit: str) -> str:
        units_map = {x.value.lower(): x for x in Units}
        unit_safe = units_map.get(unit.lower())
        if unit_safe is None:
            raise InvalidUnit
        return unit_safe

    @staticmethod
    def get_scale_factor(from_unit, to_unit) -> float:
        if isinstance(from_unit, str):
            from_unit = Units.from_str(from_unit)
        if isinstance(to_unit, str):
            to_unit = Units.from_str(to_unit)

        scale_map = {
            (Units.MM, Units.M): 0.001,
            (Units.M, Units.M): 1.0,
            (Units.MM, Units.MM): 1.0,
            (Units.M, Units.MM): 1000.0,
        }
        result = scale_map.get((from_unit, to_unit))
        if result is None:
            raise InvalidUnit(f"Unable to convert {from_unit=} {to_unit=}")
        return result

    @staticmethod
    def get_general_point_tol(units: str | Units):
        if isinstance(units, str):
            units = Units.from_str(units)

        if units == Units.MM:
            tol = Config().general_mmtol
        elif units == Units.M:
            tol = Config().general_mtol
        else:
            raise ValueError(f'Unknown unit "{units}"')
        return tol
