import re
from typing import TYPE_CHECKING

from ada.core.utils import Counter
from ada.fem import Mass
from ada.fem.containers import FemElements
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes import definitions as shape_def

from .helper_utils import _re_in, get_set_from_assembly

if TYPE_CHECKING:
    from ada import FEM


def get_mass_from_bulk(bulk_str, parent: "FEM") -> FemElements:
    """

    *MASS,ELSET=MASS3001
    2.00000000E+03,

    :return:
    """
    mass_ids = Counter(int(parent.elements.max_el_id + 1))

    re_masses = re.compile(
        r"\*(?P<mass_type>Nonstructural Mass|Mass|Rotary Inertia),\s*elset=(?P<elset>.*?)"
        r"(?:,\s*type=(?P<ptype>.*?)\s*|\s*)(?:, units=(?P<units>.*?)|\s*)\n\s*(?P<mass>.*?)$",
        _re_in,
    )
    return FemElements((get_mass(m, parent, mass_ids) for m in re_masses.finditer(bulk_str)), fem_obj=parent)


aba_to_ada_mass_map = {"ROTARY INERTIA": shape_def.MassTypes.ROTARYI, "MASS": shape_def.MassTypes.MASS}
ada_to_aba_mass_map = {val: key for key, val in aba_to_ada_mass_map.items()}


def get_mass(match, parent: "FEM", mass_id_gen):
    d = match.groupdict()
    elset = get_set_from_assembly(d["elset"], parent, "elset")
    mass_type = d["mass_type"]
    mass_type_general = aba_to_ada_mass_map.get(mass_type.upper(), None)
    if mass_type_general is None:
        raise NotImplementedError(f'Mass type "{mass_type}" is not yet supported by general ADA')

    p_type = d["ptype"]
    mass_ints = [str_to_int(x.strip()) for x in d["mass"].split(",") if x.strip() != ""]
    if len(mass_ints) == 1:
        mass_ints = mass_ints[0]
    units = d["units"]
    elem = elset.members[0]
    mass = Mass(
        d["elset"], elset, mass_ints, mass_type_general, p_type, mass_id=next(mass_id_gen), units=units, parent=parent
    )
    elem.mass_prop = mass
    return mass
