import logging

import h5py
import numpy as np

from ada.core.containers import Nodes
from ada.fem import FEM, Elem, FemSet
from ada.fem.containers import FemElements, FemSets

from .common import med_to_abaqus_type


def read_fem(assembly, fem_file, fem_name=None):
    """

    :param assembly:
    :param fem_file:
    :param fem_name:
    :return:
    """
    from ada import Node, Part

    f = h5py.File(fem_file, "r")

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
    fem = FEM(mesh_name)

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
        logging.warning("No node information is found on MED file")
        point_num = np.arange(1, len(points) + 1)

    fem._nodes = Nodes([Node(p, point_num[i]) for i, p in enumerate(points)], parent=fem)

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
            logging.warning("Point elements are still not supported")
            continue

        cell_type = med_to_abaqus_type(med_cell_type)
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
                element_sets[key] += [element_block[i] for i in val]

    fem._elements = FemElements(elements, fem)

    elsets = []
    for name, values in element_sets.items():
        elsets.append(FemSet(name, values, "elset", parent=fem))

    fem._sets = FemSets(elsets + point_sets, fem_obj=fem)

    assembly.add_part(Part(fem_name, fem=fem))
    return


def _cell_tag_to_set(cell_data_array, cell_tags):
    """
    For a single element type convert tag data into set data

    :param cell_data_array:
    :param cell_tags:
    :return: Cell Sets dictionary
    """
    cell_sets = dict()
    shared_sets = []
    for tag_id, tag_names in cell_tags.items():
        if len(tag_names) > 1:
            for v in tag_names:
                res = np.where(cell_data_array == tag_id)[0]
                if len(res) > 0:
                    shared_sets.append((v, res))
        else:
            tag_name = tag_names[0]
            res = np.where(cell_data_array == tag_id)[0]
            if len(res) > 0:
                cell_sets[tag_name] = res

    for v, s in shared_sets:
        if v in cell_sets.keys():
            cell_sets[v] = np.concatenate([cell_sets[v], s])
        else:
            cell_sets[v] = s

    return cell_sets


def _point_tags_to_sets(tags, point_tags, fem):
    """

    :param tags:
    :param point_tags:
    :return: Point sets dictionary
    """
    point_sets = dict()
    shared_sets = []
    for key, val in point_tags.items():
        if len(val) > 1:
            for set_name in val:
                shared_sets.append((set_name, np.where(tags == key)[0]))
        else:
            point_sets[val[0]] = np.where(tags == key)[0]

    for set_name, s in shared_sets:
        point_sets[set_name] = np.concatenate([point_sets[set_name], s])

    nsets = [FemSet(pn, [fem.nodes.from_id(i + 1) for i in ps], "nset", parent=fem) for pn, ps in point_sets.items()]
    return nsets


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


def _read_families(fas_data):
    families = {}
    for _, node_set in fas_data.items():
        set_id = node_set.attrs["NUM"]  # unique set id
        n_subsets = node_set["GRO"].attrs["NBR"]  # number of subsets
        nom_dataset = node_set["GRO"]["NOM"][()]  # (n_subsets, 80) of int8
        name = [None] * n_subsets
        for i in range(n_subsets):
            name[i] = "".join([chr(x) for x in nom_dataset[i]]).strip().rstrip("\x00")
        families[set_id] = name
    return families
