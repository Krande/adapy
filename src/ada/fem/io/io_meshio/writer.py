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
    from . import ada_to_meshio_type

    def get_nids(el):
        return [n.id for n in el.nodes]

    if scratch_dir is None:
        scratch_dir = _Settings.scratch_dir

    mesh_format = metadata["fem_format"]
    analysis_dir = os.path.join(scratch_dir, name)

    os.makedirs(analysis_dir, exist_ok=True)

    for p in assembly.get_all_parts_in_assembly(include_self=True):
        # assert isinstance(p, Part)
        cells = []
        plist = list(sorted(p.fem.nodes, key=attrgetter("id")))
        if len(plist) == 0:
            continue

        pid = plist[-1].id
        points = np.zeros((int(pid + 1), 3))

        def pmap(n):
            points[n.id] = n.p

        list(map(pmap, p.fem.nodes))
        for group, elements in groupby(p.fem.elements, key=attrgetter("type")):
            if group in ElemShapes.masses + ElemShapes.springs:
                # Do something
                continue
            med_el = ada_to_meshio_type[group]
            el_mapped = np.array(list(map(get_nids, elements)))
            cells.append((med_el, el_mapped))

        cell_sets = dict()
        for setid, elset in p.fem.sets.elements.items():
            # cell_sets[setid] = dict()
            # for group, elements in groupby(elset, key=attrgetter('type')):
            #     if group in ['MASS']:
            #         # Do something
            #         continue
            #     med_el = ada_to_meshio_type[group]
            cell_sets[setid] = np.array([[el.id for el in elset.members]])

        point_sets = dict()
        for setid, nset in p.fem.sets.nodes.items():
            point_sets[setid] = np.array([el.id for el in nset.members])

        mesh = meshio.Mesh(points, cells, point_sets=point_sets, cell_sets=cell_sets)
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
