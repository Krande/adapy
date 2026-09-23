from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.utils import Counter
from ada.fem import Mass
from ada.fem.containers import FemElements
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes import definitions as shape_def

from .helper_utils import get_set_from_assembly
from .keywords import validate
from .lexer import Card, iter_cards

if TYPE_CHECKING:
    from ada import FEM


def get_mass_from_bulk(bulk_str, parent: "FEM") -> FemElements:
    """

    *MASS,ELSET=MASS3001
    2.00000000E+03,

    :return:
    """
    mass_ids = Counter(int(parent.elements.max_el_id + 1))

    masses = (
        get_mass(card, parent, mass_ids)
        for card in iter_cards(bulk_str, "MASS", "ROTARY INERTIA", "NONSTRUCTURAL MASS")
    )
    return FemElements((m for m in masses if m is not None), fem_obj=parent)


aba_to_ada_mass_map = {
    "ROTARY INERTIA": shape_def.MassTypes.ROTARYI,
    "MASS": shape_def.MassTypes.MASS,
    "NONSTRUCTURAL MASS": shape_def.MassTypes.NONSTRUCTURAL,
}
ada_to_aba_mass_map = {val: key for key, val in aba_to_ada_mass_map.items()}


def get_mass(card: Card, parent: "FEM", mass_id_gen):
    validate(card)
    if not card.data_lines:
        logger.warning("abaqus read: *%s (line %d) has no data line — skipping", card.keyword, card.lineno)
        return None
    elset_name = card.params.get("ELSET")
    elset = get_set_from_assembly(elset_name, parent, "elset")
    # ``card.keyword`` is already the normalized keyword, which is exactly the map's key.
    mass_type_general = aba_to_ada_mass_map.get(card.keyword)
    if mass_type_general is None:
        raise NotImplementedError(f'Mass type "{card.keyword}" is not yet supported by general ADA')

    p_type = card.params.get("TYPE")
    mass_ints = [str_to_int(x.strip()) for x in card.data_lines[0].split(",") if x.strip() != ""]
    if len(mass_ints) == 1:
        mass_ints = mass_ints[0]
    units = card.params.get("UNITS")
    elem = elset.members[0]
    mass = Mass(
        elset_name, elset, mass_ints, mass_type_general, p_type, mass_id=next(mass_id_gen), units=units, parent=parent
    )
    elem.mass_prop = mass
    return mass
