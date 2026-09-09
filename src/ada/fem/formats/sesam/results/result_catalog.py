"""Derived field naming and manifest-presentation helpers."""

from __future__ import annotations

import re

from ada.fem.results.field_data import FieldPresentation

POSITION_LABELS = {
    "nodes": "Nodes",
    "elements": "Elements",
    "element_average": "Element average",
    "resultpoints": "Resultpoints",
}


def semantic_name(position: str, attribute: str) -> str:
    attr_key = re.sub(r"[^a-z0-9]+", "_", attribute.lower()).strip("_")
    return f"sesam.{position}.{attr_key}"


def presentation(
    position: str,
    attribute: str,
    *,
    derived: bool,
    coordinate_system: str,
    surface: str = "",
    unit: str = "",
    component_units: tuple[str, ...] = (),
) -> FieldPresentation:
    return FieldPresentation(
        semantic_key=semantic_name(position, attribute),
        group_path=(POSITION_LABELS[position], attribute),
        coordinate_system=coordinate_system,
        surface=surface,
        derived=derived,
        unit=unit,
        component_units=component_units,
    )
