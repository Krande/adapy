"""Roundtrip of adapy parametric cross-section parameters through IFC.

adapy writes several section types to IFC as *polyline* profiles
(``IfcArbitraryClosedProfileDef`` / ``IfcArbitraryProfileDefWithVoids``), which on
their own carry no parametric intent (height, web/flange thickness, ...). To make
the adapy -> IFC -> adapy round-trip lossless we additionally attach the parametric
parameters directly to the profile via the schema-native ``IfcProfileProperties``
(IFC4: ``IfcProfileDef.HasProperties``). On import these are preferred over both the
profile name string and the geometry, so the exact section is reconstructed.

For foreign IFC (no adapy params) the importer falls back to reconstructing the
section from the polyline geometry (see :mod:`ada.sections.from_geometry`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.base.units import Units
from ada.sections.categories import BaseTypes

if TYPE_CHECKING:
    import ifcopenshell

    from ada.sections.concept import Section

# Name of the IfcProfileProperties bag holding the adapy parameters.
ADA_SECTION_PSET = "ADA_SectionParameters"

# Numeric parametric fields carried through the round-trip.
SECTION_PARAM_KEYS = ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt")

# GeneralProperties fields carried for GENERAL sections (e.g. GeniE general beams,
# which have no explicit geometry — only numeric cross-section properties). Written
# with a "gp_" prefix so they never collide with the geometric keys above.
GENERAL_PROP_FIELDS = (
    "Ax",
    "Ix",
    "Iy",
    "Iz",
    "Iyz",
    "Wxmin",
    "Wymin",
    "Wzmin",
    "Shary",
    "Sharz",
    "Shceny",
    "Shcenz",
    "Sy",
    "Sz",
    "Sfy",
    "Sfz",
    "Cy",
    "Cz",
    "Cgy",
    "Cgz",
)
_GP_PREFIX = "gp_"

# Only these (fully parametric) types get an ADA parameter bag. POLY and GENERAL
# carry their definition in the geometry itself, so re-import reconstructs them
# from the curves rather than from parameters.
_PARAM_TYPES = (
    BaseTypes.BOX,
    BaseTypes.IPROFILE,
    BaseTypes.TPROFILE,
    BaseTypes.ANGULAR,
    BaseTypes.CHANNEL,
    BaseTypes.FLATBAR,
    BaseTypes.TUBULAR,
    BaseTypes.CIRCULAR,
)


def section_param_props(section: Section) -> dict | None:
    """Parameter dict for ``section``, or ``None`` if it carries no parameters.

    GENERAL sections (no explicit geometry) serialize their GeneralProperties;
    POLY sections carry their definition in the geometry and return ``None``.
    """
    if section.type == BaseTypes.GENERAL:
        props = _general_param_props(section)
    elif section.type in _PARAM_TYPES:
        props = {"sec_type": section.type.value}
        for key in SECTION_PARAM_KEYS:
            value = getattr(section, key)
            if value is not None:
                props[key] = float(value)
    else:
        return None

    if props is None:
        return None

    try:
        sec_str = section.sec_str
    except Exception:
        sec_str = None
    if sec_str:
        props["sec_str"] = sec_str

    return props


def _general_param_props(section: Section) -> dict | None:
    gp = section.properties
    if gp is None:
        return None
    props: dict[str, float | str] = {"sec_type": BaseTypes.GENERAL.value}
    for field in GENERAL_PROP_FIELDS:
        value = getattr(gp, field, None)
        if value is not None:
            props[_GP_PREFIX + field] = float(value)
    return props


def section_from_param_dict(name: str | None, props: dict, units: Units = Units.M) -> Section:
    """Rebuild a :class:`Section` from an ADA parameter dict."""
    from ada.sections.concept import GeneralProperties, Section

    if props.get("sec_type") == BaseTypes.GENERAL.value:
        gp_kwargs = {
            field: float(props[_GP_PREFIX + field])
            for field in GENERAL_PROP_FIELDS
            if props.get(_GP_PREFIX + field) is not None
        }
        return Section(
            name=name,
            sec_type=BaseTypes.GENERAL,
            genprops=GeneralProperties(**gp_kwargs),
            sec_str=props.get("sec_str"),
            units=units,
        )

    kwargs = {k: float(props[k]) for k in SECTION_PARAM_KEYS if props.get(k) is not None}
    return Section(
        name=name,
        sec_type=props["sec_type"],
        sec_str=props.get("sec_str"),
        units=units,
        **kwargs,
    )


def write_profile_section_props(f: ifcopenshell.file, profile, section: Section) -> None:
    """Attach an ``IfcProfileProperties`` parameter bag to ``profile`` (if parametric)."""
    from .utils import ifc_value_map

    params = section_param_props(section)
    if not params:
        return

    properties = [
        f.create_entity("IfcPropertySingleValue", Name=key, NominalValue=ifc_value_map(f, value))
        for key, value in params.items()
    ]
    f.create_entity(
        "IfcProfileProperties",
        Name=ADA_SECTION_PSET,
        Properties=properties,
        ProfileDefinition=profile,
    )


def read_profile_section_params(profile_def) -> dict | None:
    """Read the ADA parameter dict attached to ``profile_def``, or ``None``.

    Uses the IFC4 ``HasProperties`` inverse when available. (IFC2x3 has no such
    inverse on ``IfcProfileDef``; those files fall back to geometry on import.)
    """
    has_properties = getattr(profile_def, "HasProperties", None)
    if not has_properties:
        return None

    for prop_set in has_properties:
        if not prop_set.is_a("IfcProfileProperties"):
            continue
        if prop_set.Name != ADA_SECTION_PSET:
            continue
        return _single_values_to_dict(prop_set.Properties)

    return None


def _single_values_to_dict(properties) -> dict:
    out = {}
    for prop in properties or []:
        if not prop.is_a("IfcPropertySingleValue"):
            continue
        nominal = prop.NominalValue
        out[prop.Name] = nominal.wrappedValue if nominal is not None else None
    return out
