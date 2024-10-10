import os
from typing import Union

import meshio
import numpy as np

from ada.api.spatial import Assembly
from ada.config import Config, logger
from ada.fem import FEM
from ada.fem.formats.general import FEATypes
from ada.fem.shapes.definitions import MassTypes, SpringTypes


def meshio_to_fem(assembly: Assembly, name: str, scratch_dir=None, metadata=None, model_data_only=False) -> None:
    """Convert Assembly information to FEM using Meshio"""
    if scratch_dir is None:
        scratch_dir = Config().fea_scratch_dir

    mesh_format = metadata["fem_format"]

    mesh_name = mesh_format
    if isinstance(mesh_format, FEATypes):
        mesh_name = mesh_format.value

    analysis_dir = os.path.join(scratch_dir, name)

    os.makedirs(analysis_dir, exist_ok=True)
    prefix_mapper = {FEATypes.ABAQUS: "inp"}
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        mesh = fem_to_meshio(p.fem)
        if mesh is None:
            continue

        if mesh_format in [FEATypes.ABAQUS]:
            prefix = prefix_mapper[mesh_format]
        else:
            prefix = mesh_format

        part_file = os.path.join(analysis_dir, f"{p.name}_bulk/{p.name}.{prefix}")
        os.makedirs(os.path.dirname(part_file), exist_ok=True)
        meshio.write(
            part_file,  # str, os.PathLike, or buffer/ open file
            mesh,
            file_format=mesh_name,
        )
        print(f'Exported "{mesh_format}" using meshio to "{analysis_dir}"')


def fem_to_meshio(fem: FEM) -> Union[meshio.Mesh, None]:
    from .common import ada_to_meshio

    if len(fem.nodes) == 0:
        logger.debug(f"Attempt to convert empty FEM mesh for ({fem.name}) aborted")
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
    for element_type, elements in fem.elements.group_by_type():
        if isinstance(element_type, (MassTypes, SpringTypes)):
            logger.warning("NotImplemented: Skipping Mass or Spring Elements")
            continue
        med_el = ada_to_meshio[element_type]
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
