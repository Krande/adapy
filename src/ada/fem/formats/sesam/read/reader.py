from __future__ import annotations

import os

from ada.api.spatial import Assembly, Part
from ada.config import logger

from .read_constraints import get_bcs, get_constraints
from .read_elements import get_elements, get_mass, get_springs
from .read_materials import get_materials
from .read_nodes import get_nodes, renumber_nodes
from .read_sections import get_sections
from .read_sets import get_sets


def read_fem(fem_file: os.PathLike, fem_name: str = None):
    """Import contents from a Sesam fem file into an assembly object"""

    logger.info("Starting import of Sesam input file")
    part_name = "T1" if fem_name is None else fem_name
    with open(fem_file, "r") as d:
        part = read_sesam_fem(d.read(), part_name)

    return Assembly("TempAssembly") / part


def read_sesam_fem(bulk_str, part_name) -> Part:
    """Reads the content string of a Sesam input file and converts it to FEM objects"""

    part = Part(part_name)
    fem = part.fem

    fem.nodes = get_nodes(bulk_str, fem)
    elements, mass_elem, spring_elem, el_id_map = get_elements(bulk_str, fem)
    fem.elements = elements
    fem.elements.build_sets()
    part._materials = get_materials(bulk_str, part)
    fem.sections = get_sections(bulk_str, fem, mass_elem, spring_elem)
    fem.elements += get_mass(bulk_str, part.fem, mass_elem)
    fem.springs = get_springs(bulk_str, fem, spring_elem)
    fem.sets = part.fem.sets + get_sets(bulk_str, fem)
    fem.constraints.update(get_constraints(bulk_str, fem))
    fem.bcs += get_bcs(bulk_str, fem)
    renumber_nodes(bulk_str, fem)
    fem.elements.renumber(renumber_map=el_id_map)

    print(8 * "-" + f'Imported "{fem.instance_name}"')
    return part
