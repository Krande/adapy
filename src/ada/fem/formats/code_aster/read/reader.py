from __future__ import annotations

import os
from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.api.containers import Nodes
from ada.config import logger
from ada.fem import Elem
from ada.fem.containers import FemElements, FemSets

from ..common import med_to_ada_type
from .read_sets import (
    _cell_tag_to_set,
    _element_set_dict_to_list_of_femset,
    _point_tags_to_sets,
    _read_families,
)

if TYPE_CHECKING:
    from ada.api.spatial import Assembly
    from ada.fem import FEM


def read_fem(fem_file: os.PathLike, fem_name: str = None) -> Assembly:
    from ada import Assembly, Part

    fem = med_to_fem(fem_file, fem_name)
    if fem_name is None:
        fem_name = fem.name

    return Assembly("TempAssembly") / Part(fem_name, fem=fem)


def med_to_fem(fem_file, fem_name) -> FEM:
    from ada import FEM, Node
    from ada.config import Config

    if Config().meshing_array_backed:
        return _med_to_fem_array(fem_file, fem_name)

    with h5py.File(fem_file, "r") as f:
        # Mesh ensemble
        mesh_ensemble = f["ENS_MAA"]
        meshes = mesh_ensemble.keys()
        if len(meshes) != 1:
            raise ValueError("Must only contain exactly 1 mesh, found {}.".format(len(meshes)))

        mesh_name = list(meshes)[0]
        mesh = mesh_ensemble[mesh_name]

        dim = mesh.attrs["ESP"]

        fem_name = fem_name if fem_name is not None else mesh_name

        # Initialize FEM object
        fem = FEM(fem_name)

        # Possible time-stepping
        if "NOE" not in mesh:
            # One needs NOE (node) and MAI (French maillage, meshing) data. If they
            # are not available in the mesh, check for time-steppings.
            time_step = mesh.keys()
            if len(time_step) != 1:
                raise ValueError(f"Must only contain exactly 1 time-step, found {len(time_step)}.")
            mesh = mesh[list(time_step)[0]]

        # Points
        pts_dataset = mesh["NOE"]["COO"]
        n_points = pts_dataset.attrs["NBR"]
        points = pts_dataset[()].reshape((n_points, dim), order="F")

        if "NUM" in mesh["NOE"]:
            point_num = list(mesh["NOE"]["NUM"])
        else:
            logger.warning("No node information is found on MED file")
            point_num = np.arange(1, len(points) + 1)

        fem.nodes = Nodes([Node(p, point_num[i], parent=fem) for i, p in enumerate(points)], parent=fem)

        # Point tags
        tags = None
        if "FAM" in mesh["NOE"]:
            tags = mesh["NOE"]["FAM"][()]

        # Information for point tags
        point_tags = {}
        fas = mesh["FAS"] if "FAS" in mesh else f["FAS"][mesh_name]
        if "NOEUD" in fas:
            point_tags = _read_families(fas["NOEUD"])

        point_sets = _point_tags_to_sets(tags, point_tags, fem) if tags is not None else []

        # Information for cell tags
        cell_tags = {}
        if "ELEME" in fas:
            cell_tags = _read_families(fas["ELEME"])

        # CellBlock
        cell_types = []
        med_cells = mesh["MAI"]

        elements = []
        element_sets = dict()
        for med_cell_type, med_cell_type_group in med_cells.items():
            if med_cell_type == "PO1":
                logger.warning("Point elements are still not supported")
                continue

            cell_type = med_to_ada_type(med_cell_type)
            cell_types.append(cell_type)

            nod = med_cell_type_group["NOD"]
            n_cells = nod.attrs["NBR"]
            nodes_in = nod[()].reshape(n_cells, -1, order="F")

            if "NUM" in med_cell_type_group.keys():
                num = list(med_cell_type_group["NUM"])
            else:
                num = np.arange(0, len(nodes_in))

            element_block = [
                Elem(num[i], [fem.nodes.from_id(e) for e in c], cell_type, parent=fem) for i, c in enumerate(nodes_in)
            ]
            elements += element_block
            # Cell tags
            if "FAM" in med_cell_type_group:
                cell_data = med_cell_type_group["FAM"][()]
                cell_type_sets = _cell_tag_to_set(cell_data, cell_tags)
                for key, val in cell_type_sets.items():
                    if key not in element_sets.keys():
                        element_sets[key] = []
                    # TODO: set(val) returns unordered list
                    element_sets[key] += [element_block[i] for i in set(val)]

    fem.elements = FemElements(elements, fem_obj=fem)

    elsets = _element_set_dict_to_list_of_femset(element_sets, fem)

    fem.sets = FemSets(elsets + point_sets, parent=fem)
    return fem


def _med_to_fem_array(fem_file, fem_name) -> FEM:
    """Substrate-direct MED read: MED is already h5py array-based, so build a
    MeshArrays straight from the COO / NOD datasets (no object Node/Elem)."""
    from ada import FEM
    from ada.api.mesh.containers import ArrayElements, ArrayNodes
    from ada.api.mesh.store import MeshArrays

    with h5py.File(fem_file, "r") as f:
        mesh_ensemble = f["ENS_MAA"]
        meshes = list(mesh_ensemble.keys())
        if len(meshes) != 1:
            raise ValueError(f"Must only contain exactly 1 mesh, found {len(meshes)}.")
        mesh_name = meshes[0]
        mesh = mesh_ensemble[mesh_name]
        dim = mesh.attrs["ESP"]
        fem_name = fem_name if fem_name is not None else mesh_name
        if "NOE" not in mesh:
            time_step = list(mesh.keys())
            if len(time_step) != 1:
                raise ValueError(f"Must only contain exactly 1 time-step, found {len(time_step)}.")
            mesh = mesh[time_step[0]]

        pts_dataset = mesh["NOE"]["COO"]
        n_points = pts_dataset.attrs["NBR"]
        points = pts_dataset[()].reshape((n_points, dim), order="F")
        if dim == 2:
            points = np.column_stack([points, np.zeros(n_points)])
        if "NUM" in mesh["NOE"]:
            point_num = np.asarray(mesh["NOE"]["NUM"], dtype=np.int64)
        else:
            logger.warning("No node information is found on MED file")
            point_num = np.arange(1, len(points) + 1, dtype=np.int64)

        store = MeshArrays(np.ascontiguousarray(points, dtype=np.float64), point_num)

        tags = mesh["NOE"]["FAM"][()] if "FAM" in mesh["NOE"] else None
        fas = mesh["FAS"] if "FAS" in mesh else f["FAS"][mesh_name]
        point_tags = _read_families(fas["NOEUD"]) if "NOEUD" in fas else {}
        cell_tags = _read_families(fas["ELEME"]) if "ELEME" in fas else {}

        element_sets: dict = {}
        for med_cell_type, grp in mesh["MAI"].items():
            if med_cell_type == "PO1":
                logger.warning("Point elements are still not supported")
                continue
            cell_type = med_to_ada_type(med_cell_type)
            nod = grp["NOD"]
            n_cells = nod.attrs["NBR"]
            nodes_in = nod[()].reshape(n_cells, -1, order="F")
            num = np.asarray(grp["NUM"], dtype=np.int64) if "NUM" in grp.keys() else np.arange(n_cells, dtype=np.int64)
            store.add_elem_block_from_id_conn(cell_type, num, np.asarray(nodes_in, dtype=np.int64))
            if "FAM" in grp:
                cell_type_sets = _cell_tag_to_set(grp["FAM"][()], cell_tags)
                for key, rows in cell_type_sets.items():
                    element_sets.setdefault(key, [])
                    element_sets[key] += [int(num[i]) for i in set(rows)]

    fem = FEM(fem_name)
    fem.nodes = ArrayNodes(store, parent=fem)
    fem.elements = ArrayElements(store, fem_obj=fem)

    point_sets = _point_tags_to_sets(tags, point_tags, fem) if tags is not None else []
    elsets = _element_set_dict_to_list_of_femset(element_sets, fem)
    fem.sets = FemSets(elsets + point_sets, parent=fem)
    return fem


def _read_nodal_data(med_data, profiles):
    profile = med_data["NOE"].attrs["PFL"]
    data_profile = med_data["NOE"][profile]
    n_points = data_profile.attrs["NBR"]
    if profile.decode() == "MED_NO_PROFILE_INTERNAL":  # default profile with everything
        values = data_profile["CO"][()].reshape(n_points, -1, order="F")
    else:
        n_data = profiles[profile].attrs["NBR"]
        index_profile = profiles[profile]["PFL"][()] - 1
        values_profile = data_profile["CO"][()].reshape(n_data, -1, order="F")
        values = np.full((n_points, values_profile.shape[1]), np.nan)
        values[index_profile] = values_profile
    if values.shape[-1] == 1:  # cut off for scalars
        values = values[:, 0]
    return values


def _read_cell_data(med_data, profiles):
    profile = med_data.attrs["PFL"]
    data_profile = med_data[profile]
    n_cells = data_profile.attrs["NBR"]
    n_gauss_points = data_profile.attrs["NGA"]
    if profile.decode() == "MED_NO_PROFILE_INTERNAL":  # default profile with everything
        values = data_profile["CO"][()].reshape(n_cells, n_gauss_points, -1, order="F")
    else:
        n_data = profiles[profile].attrs["NBR"]
        index_profile = profiles[profile]["PFL"][()] - 1
        values_profile = data_profile["CO"][()].reshape(n_data, n_gauss_points, -1, order="F")
        values = np.full((n_cells, values_profile.shape[1], values_profile.shape[2]), np.nan)
        values[index_profile] = values_profile

    # Only 1 data point per cell, shape -> (n_cells, n_components)
    if n_gauss_points == 1:
        values = values[:, 0, :]
        if values.shape[-1] == 1:  # cut off for scalars
            values = values[:, 0]
    return values
