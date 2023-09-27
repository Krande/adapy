from typing import TYPE_CHECKING, Iterable

from ada.core.utils import NewLine
from ada.fem import Mass

from ..read.read_masses import ada_to_aba_mass_map
from .helper_utils import get_instance_name

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import FemSet


def masses_str(fem: "FEM", written_on_assembly_level: bool):
    if len(list(fem.elements.masses)) == 0:
        return "** No Masses"

    return "\n".join([mass_str(m, written_on_assembly_level) for m in fem.elements.masses])


def mass_str(mass: Mass, written_on_assembly_level: bool) -> str:
    if mass.point_mass_type in (Mass.PTYPES.ISOTROPIC, None):
        type_str = ""
    else:
        aba_type = ada_to_aba_mass_map.get(mass.point_mass_type, None)
        if aba_type is None:
            raise NotImplementedError()
        type_str = f", type={aba_type}"

    mstr = ",".join([str(x) for x in mass.mass]) if isinstance(mass.mass, list) else str(mass.mass)

    if mass.elset is not None:
        set_ref = mass.elset
    elif mass.fem_set is not None:
        set_ref = mass.fem_set
    else:
        raise ValueError("Unable to find proper reference to masses")
    set_name = get_instance_name(set_ref, written_on_assembly_level=written_on_assembly_level)
    if mass.type == Mass.TYPES.MASS:
        return f"""*Mass, elset={set_name}{type_str}\n {mstr}"""
    elif mass.type == Mass.TYPES.NONSTRU:
        return f"""*Nonstructural Mass, elset={set_name}, units={mass.units}\n  {mstr}"""
    elif mass.type == Mass.TYPES.ROT_INERTIA:
        return f"""*Rotary Inertia, elset={set_name}\n  {mstr}"""
    else:
        raise ValueError(f'Mass type "{mass.type}" is not supported by Abaqus')


def write_mass_elem(eltype: str, elset: "FemSet", fem: "FEM", elements: Iterable[Mass], alevel: bool) -> str:
    el_type = fem.options.ABAQUS.default_elements.get_element_type(eltype)
    el_set_str = f", ELSET={elset.name}" if elset is not None else ""
    if elset is None:
        return "** Masses not assigned to element sets\n"
    el_str = "\n".join((write_mass(el, alevel) for el in elements))
    return f"""*ELEMENT, type={el_type}{el_set_str}\n{el_str}\n"""


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
