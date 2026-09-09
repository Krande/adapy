"""The handful of DEXPI attributes adapy actually models, and how to find them.

DEXPI names an attribute by its RDL *assignment class* -- ``TagNameAssignmentClass`` in a Proteus
``<GenericAttribute Name=...>``, and the same thing shortened to the property name ``TagName`` in a
DEXPI 2.0 ``<Data property=...>``. The constants below are the Proteus spelling; every lookup here
accepts either form, so a caller never has to know which flavour the document came from.

Everything else stays where the reader put it. This module is the short list of attributes that
drive geometry and topology -- what an item is called, what flows through it, how wide it is, and
which piping class it belongs to.

The lookups take a parsed item duck-typed: anything with an ``attributes`` sequence whose entries
expose ``name`` and ``value`` will do, which is what ``model.DexpiItem`` provides.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import units

__all__ = [
    "ACTUATING_SYSTEM_NUMBER",
    "EQUIPMENT_DESCRIPTION",
    "FLUID_CODE",
    "LINE_NUMBER",
    "MATERIAL_OF_CONSTRUCTION_CODE",
    "NOMINAL_DIAMETER_NUMERICAL_VALUE_REPRESENTATION",
    "NOMINAL_DIAMETER_REPRESENTATION",
    "NOMINAL_DIAMETER_TYPE_REPRESENTATION",
    "PIPING_CLASS_CODE",
    "SEGMENT_NUMBER",
    "SUB_TAG_NAME",
    "TAG_NAME",
    "description_of",
    "find_attribute",
    "material_of",
    "medium_of",
    "nominal_diameter_of",
    "property_name",
    "spec_of",
    "tag_of",
    "value_of",
]

TAG_NAME = "TagNameAssignmentClass"
SUB_TAG_NAME = "SubTagNameAssignmentClass"
LINE_NUMBER = "LineNumberAssignmentClass"
SEGMENT_NUMBER = "SegmentNumberAssignmentClass"
PIPING_CLASS_CODE = "PipingClassCodeAssignmentClass"
FLUID_CODE = "FluidCodeAssignmentClass"
NOMINAL_DIAMETER_NUMERICAL_VALUE_REPRESENTATION = "NominalDiameterNumericalValueRepresentationAssignmentClass"
NOMINAL_DIAMETER_REPRESENTATION = "NominalDiameterRepresentationAssignmentClass"
NOMINAL_DIAMETER_TYPE_REPRESENTATION = "NominalDiameterTypeRepresentationAssignmentClass"
EQUIPMENT_DESCRIPTION = "EquipmentDescriptionAssignmentClass"
MATERIAL_OF_CONSTRUCTION_CODE = "MaterialOfConstructionCodeAssignmentClass"
#: An actuating system's own identity. DEXPI numbers it after the valve it drives
#: (``PV-202.01`` for the actuator on ``PV-202``), which is the only name that tells the two
#: apart -- an actuator borrowing its valve's tag reads as a second copy of the valve.
ACTUATING_SYSTEM_NUMBER = "ActuatingSystemNumber"

# The RDL role suffixes DEXPI appends to a property name when it names the attribute itself.
_ROLE_SUFFIXES = ("AssignmentClass", "Specialization")


def property_name(attribute_name: str) -> str:
    """The DEXPI 2.0 property name behind a Proteus assignment-class name.

    ``TagNameAssignmentClass`` -> ``TagName``. ``Representation`` is deliberately *not* stripped --
    ``NominalDiameterRepresentation`` is a property in its own right -- so only the RDL role
    suffixes come off.
    """
    for suffix in _ROLE_SUFFIXES:
        if attribute_name.endswith(suffix):
            return attribute_name[: -len(suffix)]
    return attribute_name


def _aliases(name: str) -> set[str]:
    return {name, property_name(name)}


def _iter_attributes(item: Any) -> Iterable[Any]:
    return getattr(item, "attributes", None) or ()


def find_attribute(item: Any, *names: str) -> Any | None:
    """First attribute on ``item`` matching any of ``names``, in the order given.

    Matching is on the assignment-class name or its DEXPI 2.0 property name, so a caller passes one
    constant and gets a hit on either flavour.
    """
    for name in names:
        wanted = _aliases(name)
        for attribute in _iter_attributes(item):
            if getattr(attribute, "name", None) in wanted:
                return attribute
    return None


def value_of(item: Any, *names: str) -> str | None:
    """Value of the first matching attribute, or None. Empty strings count as absent."""
    attribute = find_attribute(item, *names)
    if attribute is None:
        return None
    value = getattr(attribute, "value", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def tag_of(item: Any) -> str | None:
    """The item's tag. Falls back to the sub-tag, which is what a nozzle or chamber carries."""
    return value_of(item, TAG_NAME, SUB_TAG_NAME)


def medium_of(item: Any) -> str | None:
    """Fluid code -- the process medium, e.g. ``"PW"``."""
    return value_of(item, FLUID_CODE)


def spec_of(item: Any) -> str | None:
    """Piping class code -- the piping spec the item is built to."""
    return value_of(item, PIPING_CLASS_CODE)


def description_of(item: Any) -> str | None:
    return value_of(item, EQUIPMENT_DESCRIPTION)


def material_of(item: Any) -> str | None:
    return value_of(item, MATERIAL_OF_CONSTRUCTION_CODE)


def nominal_diameter_of(item: Any) -> float | None:
    """Nominal diameter in **metres**, or None if the item does not declare one.

    Prefers the numerical value plus its type representation (``100`` + ``"DN"``), because that
    pair is unambiguous. Falls back to parsing the human-readable representation (``"DN 100"``),
    which is all some emitters write.
    """
    kind = value_of(item, NOMINAL_DIAMETER_TYPE_REPRESENTATION)

    numeric = find_attribute(item, NOMINAL_DIAMETER_NUMERICAL_VALUE_REPRESENTATION)
    if numeric is not None:
        value = getattr(numeric, "value", None)
        try:
            return units.nominal_diameter_to_m(float(value), kind)
        except (TypeError, ValueError):
            # A numerical-value slot holding something non-numeric; fall through to the text form.
            pass

    return units.parse_nominal_diameter(value_of(item, NOMINAL_DIAMETER_REPRESENTATION), kind)
