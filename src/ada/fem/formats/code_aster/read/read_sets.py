from typing import TYPE_CHECKING, List

import numpy as np

from ada.fem import FemSet

if TYPE_CHECKING:
    from ada import FEM


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


def _point_tags_to_sets(tags, point_tags, fem: "FEM"):
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


def _element_set_dict_to_list_of_femset(element_sets, fem: "FEM") -> List[FemSet]:
    elsets = []
    for name, values in element_sets.items():
        elsets.append(FemSet(name, values, "elset", parent=fem))
    return elsets
