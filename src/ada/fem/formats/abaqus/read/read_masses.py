from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.utils import Counter
from ada.fem import Mass
from ada.fem.containers import FemElements
from ada.fem.shapes import definitions as shape_def

from .helper_utils import get_set_from_assembly
from .keywords import validate
from .lexer import KeywordBlock, iter_keywords

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
        get_mass(block, parent, mass_ids)
        for block in iter_keywords(bulk_str, "MASS", "ROTARY INERTIA", "NONSTRUCTURAL MASS")
    )
    return FemElements((m for m in masses if m is not None), fem_obj=parent)


aba_to_ada_mass_map = {
    "ROTARY INERTIA": shape_def.MassTypes.ROTARYI,
    "MASS": shape_def.MassTypes.MASS,
    "NONSTRUCTURAL MASS": shape_def.MassTypes.NONSTRUCTURAL,
}
ada_to_aba_mass_map = {val: key for key, val in aba_to_ada_mass_map.items()}


def get_mass(block: KeywordBlock, parent: "FEM", mass_id_gen):
    validate(block)
    if not block.data_lines:
        logger.warning("abaqus read: *%s (line %d) has no data line — skipping", block.keyword, block.lineno)
        return None
    elset_name = block.params.get("ELSET")
    elset = get_set_from_assembly(elset_name, parent, "elset")
    # ``block.keyword`` is already the normalized keyword, which is exactly the map's key.
    mass_type_general = aba_to_ada_mass_map.get(block.keyword)
    if mass_type_general is None:
        raise NotImplementedError(f'Mass type "{block.keyword}" is not yet supported by general ADA')

    p_type = block.params.get("TYPE")
    # Floats: these are masses and inertias. Read through str_to_int (int(float(s))) they were
    # truncated -- a 12.5 mass came back as 12.
    values = [float(x) for x in block.data_lines[0].split(",") if x.strip() != ""]
    value = values[0] if len(values) == 1 else values
    units = block.params.get("UNITS")
    mass = Mass(
        elset_name, elset, value, mass_type_general, p_type, mass_id=next(mass_id_gen), units=units, parent=parent
    )
    if mass_type_general != shape_def.MassTypes.NONSTRUCTURAL:
        # A point mass / rotary inertia belongs to its one mass element. A nonstructural mass
        # is spread over a set of STRUCTURAL elements and is no element's property.
        elset.members[0].mass_prop = mass
    return mass
