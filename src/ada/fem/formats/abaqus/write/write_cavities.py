"""Gas-filled cavities (``*Fluid Cavity``) and the initial conditions of cavities and cable sections.

A :class:`~ada.fem.FluidCavity` is written as a surface-based pneumatic fluid cavity (Abaqus Analysis User's Guide,
"Surface-based fluid cavities"):

* ``*Fluid Cavity, name=, behavior=, ref node=, surface=, ambient=`` beside the mesh it is bounded by (part or
  assembly), with ``ADIABATIC`` for an adiabatic gas and the additional volume, if any, on its data line;
* ``*Fluid Behavior`` with ``*Molecular Weight`` and ``*Capacity`` (the molar heat capacity at constant pressure,
  ``gamma R / (gamma - 1)``, constant) at model level, and ``*Physical Constants`` with the gas constant and an
  absolute zero of 0, so temperatures are kelvin;
* ``*Initial Conditions, type=FLUID PRESSURE`` (relative to the ambient pressure) and ``type=TEMPERATURE`` on the
  reference node.

A cable section's pretension is ``*Initial Conditions, type=STRESS``, the axial stress ``N / A`` on its elements; a
membrane's the in-plane stresses ``S11 = S22 = N / t`` (``S12 = 0``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import FemSet

from ..grammar import format_number, render_keyword
from .helper_utils import get_instance_name, render_block

if TYPE_CHECKING:
    from ada import FEM, Assembly
    from ada.fem.cavities import FluidCavity


def behavior_name(cavity: FluidCavity) -> str:
    return f"{cavity.name}_gas"


def surface_name(cavity: FluidCavity) -> str:
    """The surface a cavity names: its own when it is bounded by a ``Surface``, else the one written for its element
    set (:func:`fluid_cavities_str`)."""
    if isinstance(cavity.surface, FemSet):
        return f"{cavity.name}_surf"
    return cavity.surface.name


def fluid_cavities_str(fem: FEM, on_assembly_level: bool) -> str:
    """``*Fluid Cavity`` for each cavity of ``fem``, after the surface of a cavity bounded by an element set."""
    if len(fem.fluid_cavities) == 0:
        return "** No Fluid Cavities"
    blocks = []
    for cavity in fem.fluid_cavities.values():
        if isinstance(cavity.surface, FemSet):
            set_ref = get_instance_name(cavity.surface, on_assembly_level)
            blocks.append(
                render_block("Surface", [("type", "ELEMENT"), ("name", surface_name(cavity))], [f"{set_ref}, SPOS"])
            )
        gas = cavity.gas
        params = [("ADIABATIC", None)] if gas.adiabatic else []
        params += [
            ("name", cavity.name),
            ("behavior", behavior_name(cavity)),
            ("ref node", get_instance_name(cavity.ref_node, on_assembly_level)),
            ("surface", surface_name(cavity)),
            ("ambient", format_number(gas.ambient_pressure)),
        ]
        data = [f" {format_number(cavity.additional_volume)},"] if cavity.additional_volume > 0.0 else []
        blocks.append(render_block("Fluid Cavity", params, data, [f"Fluid cavity: {cavity.name}"]))
    return "\n".join(blocks)


def all_cavities(assembly: Assembly) -> list[FluidCavity]:
    return [cav for p in assembly.get_all_parts_in_assembly(include_self=True) for cav in p.fem.fluid_cavities.values()]


def fluid_behaviors_str(assembly: Assembly) -> str:
    """Model data: the gas of each cavity, and the physical constants an ideal gas needs."""
    from ada.fem.cavities import UNIVERSAL_GAS_CONSTANT

    cavities = all_cavities(assembly)
    if not cavities:
        return ""
    out = render_keyword(
        "Physical Constants",
        [("absolute zero", format_number(0.0)), ("universal gas constant", format_number(UNIVERSAL_GAS_CONSTANT))],
        comments=["Temperatures are absolute (kelvin)"],
    )
    for cavity in cavities:
        gas = cavity.gas
        out += render_keyword("Fluid Behavior", [("name", behavior_name(cavity))])
        out += render_keyword("Molecular Weight", (), [f" {format_number(gas.molecular_weight)},"])
        out += render_keyword("Capacity", [("type", "POLYNOMIAL")], [f" {format_number(gas.molar_cp)}, 0., 0., 0., 0."])
    return out[:-1]


def model_initial_conditions_str(assembly: Assembly) -> str:
    """Model data: each cavity's initial pressure and temperature, and each cable's initial axial stress."""
    blocks = []
    for cavity in all_cavities(assembly):
        node = get_instance_name(cavity.ref_node, True)
        gas = cavity.gas
        blocks.append(
            render_block(
                "Initial Conditions",
                # Unquoted, as the Keywords Guide spells it (``format_value`` quotes a value with a blank).
                [("type=FLUID PRESSURE", None)],
                [f"{node}, {format_number(gas.initial_gauge_pressure)}"],
                [f"Fluid cavity: {cavity.name}  initial pressure relative to ambient"],
            )
        )
        blocks.append(
            render_block(
                "Initial Conditions",
                [("type", "TEMPERATURE")],
                [f"{node}, {format_number(gas.initial_temperature)}"],
                [f"Fluid cavity: {cavity.name}  initial temperature"],
            )
        )
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        for fem_sec in p.fem.sections.shells:
            if not fem_sec.is_membrane or fem_sec.pretension == 0.0:
                continue
            elset = get_instance_name(fem_sec.elset, True)
            stress = format_number(fem_sec.pretension / fem_sec.thickness)
            blocks.append(
                render_block(
                    "Initial Conditions",
                    [("type", "STRESS")],
                    [f"{elset}, {stress}, {stress}, 0.0"],
                    [f"Membrane: {fem_sec.name}  pretension {format_number(fem_sec.pretension)} per unit length"],
                )
            )
        for fem_sec in p.fem.sections.lines:
            if not fem_sec.is_cable or fem_sec.pretension == 0.0:
                continue
            elset = get_instance_name(fem_sec.elset, True)
            stress = fem_sec.pretension / fem_sec.area
            blocks.append(
                render_block(
                    "Initial Conditions",
                    [("type", "STRESS")],
                    [f"{elset}, {format_number(stress)}"],
                    [f"Cable: {fem_sec.name}  pretension {format_number(fem_sec.pretension)}"],
                )
            )
    return "\n".join(blocks)
