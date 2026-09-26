from typing import TYPE_CHECKING, Iterable

from ada.core.utils import NewLine
from ada.fem import Mass

from ..grammar import Verbatim, format_number, render_keyword
from .helper_utils import get_instance_name, render_block, set_name

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import FemSet


def masses_str(fem: "FEM", written_on_assembly_level: bool):
    if len(list(fem.elements.masses)) == 0:
        return "** No Masses"

    return "\n".join([mass_str(m, written_on_assembly_level) for m in fem.elements.masses])


def mass_str(mass: Mass, written_on_assembly_level: bool) -> str:
    # *Mass's TYPE is the point-mass type -- ISOTROPIC (the default) or ANISOTROPIC. It was looked
    # up in the map of MASS/ROTARY INERTIA/NONSTRUCTURAL MASS keywords, which has neither, so an
    # anisotropic mass could not be written at all.
    ptype = mass.point_mass_type
    type_param = [] if ptype in (Mass.PTYPES.ISOTROPIC, None) else [("type", ptype)]

    values = mass.mass if isinstance(mass.mass, (list, tuple)) else [mass.mass]
    mstr = ", ".join(format_number(float(x)) for x in values)

    if mass.type == Mass.TYPES.NONSTRU and mass.fem_set is not None:
        # Spread over the STRUCTURAL elements it was defined on -- not over the one-member set
        # holding adapy's pseudo-element for it, which is where it used to land.
        set_ref = mass.fem_set
    elif mass.elset is not None:
        set_ref = mass.elset
    elif mass.fem_set is not None:
        set_ref = mass.fem_set
    else:
        raise ValueError("Unable to find proper reference to masses")
    set_name = get_instance_name(set_ref, written_on_assembly_level=written_on_assembly_level)
    if mass.type == Mass.TYPES.MASS:
        return render_block("Mass", [("elset", set_name), *type_param], [f" {mstr}"])
    elif mass.type == Mass.TYPES.NONSTRU:
        return render_block("Nonstructural Mass", [("elset", set_name), ("units", Verbatim(mass.units))], [f"  {mstr}"])
    elif mass.type == Mass.TYPES.ROT_INERTIA:
        return render_block("Rotary Inertia", [("elset", set_name)], [f"  {mstr}"])
    else:
        raise ValueError(f'Mass type "{mass.type}" is not supported by Abaqus')


def write_mass_elem(eltype: str, elset: "FemSet", fem: "FEM", elements: Iterable[Mass], alevel: bool) -> str:
    el_type = fem.options.ABAQUS.default_elements.get_element_type(eltype)
    if elset is None:
        return "** Masses not assigned to element sets\n"
    return render_keyword(
        "ELEMENT", [("type", el_type), ("ELSET", set_name(elset))], [write_mass(el, alevel) for el in elements]
    )


def write_mass(el: "Mass", alevel: bool) -> str:
    if el.nodes is None:
        return ""

    nl = NewLine(10, suffix=7 * " ")

    if len(el.nodes) > 6:
        di = " {}"
    else:
        di = "{:>13}"
    el_str = (
        f"{el.id:>7}, " + " ".join([f"{di.format(get_instance_name(no, alevel))}," + next(nl) for no in el.nodes])[:-1]
    )
    return el_str
