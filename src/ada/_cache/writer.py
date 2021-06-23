import os
import pathlib
from itertools import groupby
from operator import attrgetter

import numpy as np


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
    for p in assembly.get_all_parts_in_assembly(True):
        add_part_to_cache(p, parts_group)

    f.close()

    print(f'Saved cached model at "{cache_file_path}"')


def add_part_to_cache(part, parts_group):
    """

    :param part:
    :type part: ada.Part
    :param parts_group:
    """
    part_group = parts_group.create_group(part.name)
    # Add Materials, Sections
    # Add Beams, Plates, etc..

    # Add FEM object
    if len(part.fem.nodes) > 0:
        print(f'Caching FEM data from "{part.name}"')
        add_fem_to_cache(part.fem, part_group)


def add_fem_to_cache(fem, part_group):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :param part_group:
    """
    fem_group = part_group.create_group("FEM")
    fem_group.attrs.create("NAME", fem.name)

    # Add Nodes
    points = np.array([[n.id, *n.p] for n in fem.nodes])
    coo = fem_group.create_dataset("NODES", data=points)
    coo.attrs.create("NBR", len(points))

    # Add elements
    def get_node_ids_from_element(el_):
        return [int(n.id) for n in el_.nodes]

    elements_group = fem_group.create_group("MESH")

    for group, elements in groupby(sorted(fem.elements, key=attrgetter("type")), key=attrgetter("type")):
        elements = list(elements)
        elements_formatted = [[int(el.id), *get_node_ids_from_element(el)] for el in elements]
        med_cells = elements_group.create_group(group)
        med_cells.create_dataset("ELEMENTS", data=elements_formatted)
