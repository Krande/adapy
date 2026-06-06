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
    from ada.config import Config

    logger.info("Starting import of Sesam input file")
    part_name = "T1" if fem_name is None else fem_name

    if Config().meshing_array_backed:
        # Stream the file: never hold the whole deck as a string (the GCOORD/GELMNT1
        # bulk goes straight to arrays; the small remainder is bucketed for the
        # object section/set/constraint readers).
        part = Part(part_name)
        _read_sesam_fem_array_stream(fem_file, part)
        print(8 * "-" + f'Imported "{part.fem.instance_name}"')
        return Assembly("TempAssembly") / part

    with open(fem_file, "r") as d:
        part = read_sesam_fem(d.read(), part_name)

    return Assembly("TempAssembly") / part


def read_sesam_fem(bulk_str, part_name) -> Part:
    """Reads the content string of a Sesam input file and converts it to FEM objects"""

    from ada.config import Config

    part = Part(part_name)
    fem = part.fem

    if Config().meshing_array_backed:
        _read_sesam_fem_array(bulk_str, part)
        print(8 * "-" + f'Imported "{fem.instance_name}"')
        return part

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


def _build_array_fem(part, coords, node_ids, by_type, mass_elem, spring_elem, ext_map, reader_text) -> None:
    """Assemble the array-backed FEM from parsed mesh arrays + a (small) text blob the
    object section/set/constraint readers run over. Shared by the streaming and
    bulk-string array paths."""
    import numpy as np

    from ada.api.mesh.containers import ArrayElements, ArrayNodes
    from ada.api.mesh.store import MeshArrays

    fem = part.fem
    store = MeshArrays(coords, node_ids)
    for ctype, (el_ids, conns) in by_type.items():
        store.add_elem_block_from_id_conn(ctype, np.array(el_ids, dtype=np.int64), np.array(conns, dtype=np.int64))

    fem.nodes = ArrayNodes(store, parent=fem)
    fem.elements = ArrayElements(store, fem_obj=fem)

    part._materials = get_materials(reader_text, part)
    fem.sections = get_sections(reader_text, fem, mass_elem, spring_elem)
    fem.elements += get_mass(reader_text, part.fem, mass_elem)
    fem.springs = get_springs(reader_text, fem, spring_elem)
    fem.sets = part.fem.sets + get_sets(reader_text, fem)
    fem.constraints.update(get_constraints(reader_text, fem))
    fem.bcs += get_bcs(reader_text, fem)
    renumber_nodes(reader_text, fem)
    fem.elements.renumber(renumber_map=ext_map)


def _read_sesam_fem_array_stream(fem_file, part: Part) -> None:
    """Streaming substrate-direct read: one pass over the file handle, mesh -> arrays,
    everything else -> a small text blob. Never holds the whole deck as a string."""
    from .stream import stream_fem_mesh

    coords, node_ids, by_type, mass_elem, spring_elem, ext_map, other_text = stream_fem_mesh(fem_file)
    _build_array_fem(part, coords, node_ids, by_type, mass_elem, spring_elem, ext_map, other_text)


def _read_sesam_fem_array(bulk_str, part: Part) -> None:
    """Substrate-direct read from a full bulk string (for direct callers that already
    have the deck in memory). Prefer the streaming path via read_fem."""
    from .read_elements import get_elements_arrays
    from .read_nodes import get_nodes_arrays

    coords, node_ids = get_nodes_arrays(bulk_str)
    by_type, mass_elem, spring_elem, ext_map = get_elements_arrays(bulk_str)
    _build_array_fem(part, coords, node_ids, by_type, mass_elem, spring_elem, ext_map, bulk_str)
