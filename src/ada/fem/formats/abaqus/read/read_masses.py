import re
from typing import TYPE_CHECKING

from ada.fem import Mass
from ada.fem.formats.utils import str_to_int

from .helper_utils import _re_in

if TYPE_CHECKING:
    from ada import FEM


def get_mass_from_bulk(bulk_str, parent: "FEM"):
    """

    *MASS,ELSET=MASS3001
    2.00000000E+03,

    :return:
    """

    re_masses = re.compile(
        r"\*(?P<mass_type>Nonstructural Mass|Mass|Rotary Inertia),\s*elset=(?P<elset>.*?)"
        r"(?:,\s*type=(?P<ptype>.*?)\s*|\s*)(?:, units=(?P<units>.*?)|\s*)\n\s*(?P<mass>.*?)$",
        _re_in,
    )

    return {m.name: m for m in (get_mass(m, parent) for m in re_masses.finditer(bulk_str))}


def get_mass(match, parent: "FEM"):
    d = match.groupdict()
    elset = parent.sets.get_elset_from_name(d["elset"])
    mass_type = d["mass_type"]
    p_type = d["ptype"]
    mass_ints = [str_to_int(x.strip()) for x in d["mass"].split(",") if x.strip() != ""]
    if len(mass_ints) == 1:
        mass_ints = mass_ints[0]
    units = d["units"]
    mass = Mass(d["elset"], elset, mass_ints, mass_type, p_type, units, parent=parent)
    elem = elset.members[0]
    elem.mass_prop = mass
    return mass
