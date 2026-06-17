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
        logger.info(8 * "-" + f'Imported "{part.fem.instance_name}"')
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
        logger.info(8 * "-" + f'Imported "{fem.instance_name}"')
        return part

    fem.nodes = get_nodes(bulk_str, fem)
    elements, mass_elem, spring_elem, el_id_map = get_elements(bulk_str, fem)
    fem.elements = elements
    fem.elements.build_sets()
    part._materials = get_materials(bulk_str, part)
    fem.sections = get_sections(bulk_str, fem, mass_elem, spring_elem)
    fem.elements += get_mass(bulk_str, part.fem, mass_elem, el_id_map)
    fem.springs = get_springs(bulk_str, fem, spring_elem)
    fem.sets = part.fem.sets + get_sets(bulk_str, fem)
    fem.constraints.update(get_constraints(bulk_str, fem))
    fem.bcs += get_bcs(bulk_str, fem)
    renumber_nodes(bulk_str, fem)
    fem.elements.renumber(renumber_map=el_id_map)

    logger.info(8 * "-" + f'Imported "{fem.instance_name}"')
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
    fem.elements += get_mass(reader_text, part.fem, mass_elem, ext_map)
    fem.springs = get_springs(reader_text, fem, spring_elem)
    fem.sets = part.fem.sets + get_sets(reader_text, fem)
    fem.constraints.update(get_constraints(reader_text, fem))
    fem.bcs += get_bcs(reader_text, fem)
    node_map = renumber_nodes(reader_text, fem)
    fem.elements.renumber(renumber_map=ext_map)

    # The substrate just renumbered nodes/elements from internal -> external ids,
    # but the sets are id-backed (they captured the *internal* ids at read time, via
    # ``from_id`` against the still-internal store). The object path stays correct for
    # free because its sets hold Node/Elem objects whose ``.id`` is renumbered in place;
    # the array path must remap the captured ids explicitly or every NSET/ELSET member
    # resolves to a now-missing id (e.g. JacketHybrid elset member 787 -> external 3052).
    _remap_id_backed_sets(fem, node_map, ext_map)


def _remap_id_backed_sets(fem, node_map: dict[int, int], elem_map: dict[int, int]) -> None:
    """Remap id-backed FemSet members through the internal->external renumber maps.

    NSET members go through ``node_map``, ELSET members through ``elem_map``. Ids absent
    from a map (e.g. mass/spring-derived sets created with already-final ids) pass through
    unchanged, so the remap is idempotent and safe for the mixed set population."""
    for fs in list(fem.sets):
        if fs._member_ids is None:
            continue
        m = node_map if fs.type == "nset" else elem_map
        fs._member_ids = [m.get(mid, mid) for mid in fs._member_ids]


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
