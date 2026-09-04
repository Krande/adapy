"""Units for derived fields derived from the Sesam ``UNITS`` record."""

from __future__ import annotations

import math


_LENGTH_UNITS = (
    (1.0, "m"),
    (1.0e-3, "mm"),
    (0.0254, "in"),
    (0.3048, "ft"),
)
_FORCE_UNITS = (
    (1.0, "N"),
    (1.0e3, "kN"),
    (1.0e6, "MN"),
    (4.4482216, "lbf"),
    (4.4482216e3, "kipf"),
)


def _symbol(factor: float, known: tuple[tuple[float, str], ...]) -> str:
    for expected, symbol in known:
        if math.isclose(factor, expected, rel_tol=1.0e-7, abs_tol=1.0e-12):
            return symbol
    return ""


def result_component_units(
    unit_factors: tuple[float, float, float] | None,
    attribute: str,
    components,
) -> tuple[str, ...]:
    """Return units aligned with ``components``, or empty strings if unknown.

    The Result Interface defines result dimensions but carries no independent
    unit system; its values use the model's ``UNITS`` record. Do not invent a
    label when a conversion factor is not one of the documented unit sets.
    """

    component_names = tuple(components)
    if unit_factors is None:
        return tuple("" for _ in component_names)
    length = _symbol(float(unit_factors[0]), _LENGTH_UNITS)
    force = _symbol(float(unit_factors[1]), _FORCE_UNITS)
    if not length or not force:
        return tuple("" for _ in component_names)

    stress = "Pa" if (force, length) == ("N", "m") else f"{force}/{length}²"
    force_per_length = f"{force}/{length}"
    moment = f"{force}·{length}"

    if attribute in {"G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "B-STRESS"}:
        return tuple(stress for _ in component_names)
    if attribute == "DISPLACEMENT":
        return tuple("rad" if name in {"RX", "RY", "RZ"} else length for name in component_names)
    if attribute == "REACTION-FORCE":
        return tuple(moment if "MOMENT" in name else force for name in component_names)
    if attribute == "G-FORCE":
        return tuple(moment if name.startswith("M") else force for name in component_names)
    if attribute == "R-STRESS":
        return tuple(force if name.startswith("M") else force_per_length for name in component_names)
    return tuple("" for _ in component_names)


def common_result_unit(component_units: tuple[str, ...]) -> str:
    """Return the shared unit only when every component has the same one."""

    return component_units[0] if component_units and component_units[0] and len(set(component_units)) == 1 else ""


__all__ = ["common_result_unit", "result_component_units"]
