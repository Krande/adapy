import json
import logging
import os
import pathlib
from itertools import groupby
from operator import attrgetter

import numpy as np

from ada import Beam, Material, Part, Section
from ada.core.containers import Nodes

from .utils import to_safe_name


def write_assembly_to_cache(assembly, cache_file_path):
    """
    Write the Assembly information to a HDF5 file format for High performance cache.

    TODO: Add support for FEM only to begin with

    :param assembly:
    :param cache_file_path:
    :type assembly: ada.Assembly
    :return:
    """
    import h5py

    cache_file_path = pathlib.Path(cache_file_path)
    os.makedirs(cache_file_path.parent, exist_ok=True)
    h5_filename = cache_file_path.with_suffix(".h5")
    f = h5py.File(h5_filename, "w")

    info = f.create_group("INFO")
    info.attrs.create("NAME", assembly.name)

    parts_group = f.create_group("PARTS")

    walk_parts(parts_group, assembly)
    # for p in assembly.get_all_parts_in_assembly(True):
    #     add_part_to_cache(p, parts_group)

    f.close()

    print(f'Saved cached model at "{cache_file_path}"')


def walk_parts(cache_p, part):
    for p in part.parts.values():
        part_group = add_part_to_cache(p, cache_p)
        part_group.attrs.create("PARENT", to_safe_name(p.parent.name))
        walk_parts(part_group, p)


def add_part_to_cache(part: Part, parent_part_group):
    part_group = parent_part_group.create_group(to_safe_name(part.name))

    part_group.attrs.create("METADATA", json.dumps(part.metadata))

    if len(part.nodes) > 0:
        add_nodes_to_cache(part.nodes, part_group)

    if len(part.sections) > 0:
        add_sections_to_cache(part, part_group)

    if len(part.materials) > 0:
        add_materials_to_cache(part, part_group)

    if len(part.beams) > 0:
        add_beams_to_cache(part, part_group)

    if len(part.plates) > 0:
        add_plates_to_cache()

    if len(part.shapes) > 0:
        add_shapes_to_cache()

    if len(part.pipes) > 0:
        add_pipes_to_cache()

    if len(part.walls) > 0:
        add_walls_to_cache()

    # Add FEM object
    if len(part.fem.nodes) > 0:
        print(f'Caching FEM data from "{part.name}"')
        add_fem_to_cache(part.fem, part_group)

    return part_group


def add_sections_to_cache(part, parts_group):
    prefix = "SECTIONS"

    def add_ints_to_cache(s: Section):
        return [x if x is not None else 0 for x in [s.r, s.wt, s.h, s.w_top, s.w_btn, s.t_w, s.t_ftop, s.t_fbtn, s.id]]

    def add_strings_to_cache(s: Section):
        return [s.guid, s.name, s.units, s.type]

    parts_group.create_dataset(f"{prefix}_STR", data=[add_strings_to_cache(bm) for bm in part.sections])
    parts_group.create_dataset(f"{prefix}_INT", data=[add_ints_to_cache(bm) for bm in part.sections])


def add_materials_to_cache(part, parts_group):
    prefix = "MATERIALS"

    def add_ints_to_cache(e: Material):
        m = e.model
        return [m.E, m.rho, m.sig_y, e.id]

    def add_strings_to_cache(e: Material):
        return [e.guid, e.name, e.units]

    parts_group.create_dataset(f"{prefix}_INT", data=[add_ints_to_cache(bm) for bm in part.materials])
    parts_group.create_dataset(f"{prefix}_STR", data=[add_strings_to_cache(bm) for bm in part.materials])


def add_plates_to_cache():
    logging.error("Plate caching is not yet implemented")


def add_shapes_to_cache():
    logging.error("Shape caching is not yet implemented")


def add_pipes_to_cache():
    logging.error("Pipes caching is not yet implemented")


def add_walls_to_cache():
    logging.error("Walls caching is not yet implemented")


def add_beams_to_cache(part: Part, parts_group):
    prefix = "BEAMS"

    def add_int_cache(bm: Beam, up=False):
        if up is True:
            nids = bm.up.tolist()
        else:
            nids = [bm.n1.id, bm.n2.id]
        if None in nids:
            raise ValueError()
        return nids

    def add_str_cache(bm: Beam):
        return [bm.guid, bm.name, bm.section.name, bm.material.name, json.dumps(bm.metadata)]

    parts_group.create_dataset(f"{prefix}_INT", data=[add_int_cache(bm) for bm in part.beams])
    parts_group.create_dataset(f"{prefix}_STR", data=[add_str_cache(bm) for bm in part.beams])
    parts_group.create_dataset(f"{prefix}_UP", data=[add_int_cache(bm, True) for bm in part.beams])


def add_fem_to_cache(fem, part_group):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :param part_group:
    """
    fem_group = part_group.create_group("FEM")
    fem_group.attrs.create("NAME", to_safe_name(fem.name))

    # Add Nodes
    add_nodes_to_cache(fem.nodes, fem_group)

    # Add elements
    elements_group = fem_group.create_group("MESH")
    for group, elements in groupby(sorted(fem.elements, key=attrgetter("type")), key=attrgetter("type")):
        med_cells = elements_group.create_group(group)
        med_cells.create_dataset("ELEMENTS", data=[[int(el.id), *[int(n.id) for n in el.nodes]] for el in elements])


def add_nodes_to_cache(nodes: Nodes, group):
    points = np.array([[n.id, *n.p] for n in nodes])
    coo = group.create_dataset("NODES", data=points)
    coo.attrs.create("NBR", len(points))
