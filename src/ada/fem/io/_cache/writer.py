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

    h5_filename = cache_file_path.with_suffix(".h5")
    f = h5py.File(h5_filename, "w")

    info = f.create_group("INFO")
    info.attrs.create("MAJ", 3)
    parts_group = f.create_group("PARTS")
    for p in assembly.get_all_parts_in_assembly(True):

        add_part_to_cache(p, parts_group)

    f.close()


def add_part_to_cache(part, parts_group):
    """

    :param part:
    :type part: ada.Part
    :param parts_group:
    """
    part_group = parts_group.create_group(part.name)
    add_fem_to_cache(part.fem, part_group)


def add_fem_to_cache(fem, part_group):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :param part_group:
    """
    mesh = part_group.create_group("MESH")

    # Add elements
    def get_node_ids_from_element(el_):
        return [int(n.id) for n in el_.nodes]

    elements_group = mesh.create_group("ELEMENTS")
    for group, elements in groupby(fem.elements, key=attrgetter("type")):
        elements = list(elements)
        cells = np.array(list(map(get_node_ids_from_element, elements)))
        med_cells = elements_group.create_group(group)
        med_cells.create_dataset("NODES", data=cells.flatten(order="F"))
        med_cells.create_dataset("ELEMENTS", data=[int(el.id) for el in elements])
