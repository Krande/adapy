import logging
import os
from itertools import groupby
from operator import attrgetter

import meshio
import numpy as np

from ada.config import Settings as _Settings
from ada.fem import ElemShapes


def meshio_to_fem(
    assembly,
    name,
    scratch_dir=None,
    metadata=None,
    execute=False,
    run_ext=False,
    cpus=2,
    gpus=None,
    overwrite=False,
    exit_on_complete=True,
):
    """

    :param assembly:
    :param name:
    :param scratch_dir:
    :param metadata:
    :param execute:
    :param run_ext:
    :param cpus:
    :param gpus:
    :param overwrite:
    :param exit_on_complete:

    :type assembly: ada.Assembly
    :return:
    """
    if scratch_dir is None:
        scratch_dir = _Settings.scratch_dir

    mesh_format = metadata["fem_format"]
    analysis_dir = os.path.join(scratch_dir, name)

    os.makedirs(analysis_dir, exist_ok=True)

    for p in assembly.get_all_parts_in_assembly(include_self=True):
        mesh = fem_to_meshio(p.fem)
        if mesh is None:
            continue
        prefix_mapper = dict(abaqus="inp")
        if mesh_format in ["abaqus"]:
            prefix = prefix_mapper[mesh_format]
        else:
            prefix = mesh_format

        part_file = os.path.join(analysis_dir, f"{p.name}_bulk/{p.name}.{prefix}")
        os.makedirs(os.path.dirname(part_file), exist_ok=True)
        meshio.write(
            part_file,  # str, os.PathLike, or buffer/ open file
            mesh,
            file_format=mesh_format,
        )
        print(f'Exported "{mesh_format}" using meshio to "{analysis_dir}"')


def fem_to_meshio(fem):
    """

    :param fem: Ada FEM object
    :type fem: ada.fem.FEM
    :return: Meshio MESH object
    :rtype: meshio.Mesh
    """
    from meshio.abaqus._abaqus import abaqus_to_meshio_type

    if len(fem.nodes) == 0:
        logging.error(f"Attempt to convert empty FEM mesh for ({fem.name}) aborted")
        return None

    # Points
    points = np.zeros((int(fem.nodes.max_nid + 1), 3))

    def pmap(n):
        points[int(n.id - 1)] = n.p

    list(map(pmap, fem.nodes))

    # Elements

    def get_node_ids_from_element(el_):
        return [int(n.id - 1) for n in el_.nodes]

    cells = []
    for group, elements in groupby(fem.elements, key=attrgetter("type")):
        if group in ElemShapes.masses + ElemShapes.springs:
            logging.error("NotImplemented: Skipping Mass or Spring Elements")
            continue
        med_el = abaqus_to_meshio_type[group]
        elements = list(elements)
        el_mapped = np.array(list(map(get_node_ids_from_element, elements)))
        el_long = np.zeros((int(fem.elements.max_el_id + 1), len(el_mapped[0])))
        for el in elements:
            el_long[el.id] = get_node_ids_from_element(el)

        cells.append((med_el, el_mapped))

    cell_sets = dict()
    for set_name, elset in fem.sets.elements.items():
        cell_sets[set_name] = np.array([[el.id for el in elset.members]], dtype="int32")

    point_sets = dict()
    for set_name, nset in fem.sets.nodes.items():
        point_sets[set_name] = np.array([[el.id for el in nset.members]], dtype="int32")

    mesh = meshio.Mesh(points, cells, point_sets=point_sets, cell_sets=cell_sets)
    return mesh
