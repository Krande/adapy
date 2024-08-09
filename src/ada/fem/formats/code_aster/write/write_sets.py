from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger
from ada.fem.shapes import definitions as shape_def

from ..common import ada_to_med_type
from .helper_utils import resolve_ids_in_multiple

if TYPE_CHECKING:
    from ada.api.spatial import Part


def get_node_ids_from_element(el_):
    return [int(n.id - 1) for n in el_.nodes]


def _add_cell_sets(cells_group, part: "Part", families):
    """

    :param cells_group:
    :param part:
    :param families:
    """
    cell_id_num = -4

    element = families.create_group("ELEME")
    tags = dict()
    tags_data = dict()

    cell_id_current = cell_id_num
    for cell_set in part.fem.elsets.values():
        tags[cell_id_current] = [cell_set.name]
        tags_data[cell_id_current] = cell_set.members
        cell_id_current -= 1

    res_data = resolve_ids_in_multiple(tags, tags_data, True)

    for group, elements in part.fem.elements.group_by_type():
        elements = list(elements)
        cell_ids = {el.id: i for i, el in enumerate(elements)}

        cell_data = np.zeros(len(elements), dtype=np.int32)

        for t, mem in res_data.items():
            list_filtered = [cell_ids[el.id] for el in filter(lambda x: x.type == group, mem)]
            for index in list_filtered:
                cell_data[index] = t

        if isinstance(group, (shape_def.MassTypes, shape_def.SpringTypes)):
            cells = np.array([el.members[0].id for el in elements])
        else:
            cells = np.array(list(map(get_node_ids_from_element, elements)))

        med_type = ada_to_med_type(group, part.fem.options.CODE_ASTER.use_reduced_integration)
        med_cells = cells_group.get(med_type)
        family = med_cells.create_dataset("FAM", data=cell_data)
        family.attrs.create("CGT", 1)
        family.attrs.create("NBR", len(cells))

    _write_families(element, tags)


def _add_node_sets(nodes_group, part: "Part", points, families):
    tags = dict()
    nsets = dict()
    for key, val in part.fem.nsets.items():
        nsets[key] = [int(p.id) for p in val]

    points = _set_to_tags(nsets, points, 2, tags)

    family = nodes_group.create_dataset("FAM", data=points)
    family.attrs.create("CGT", 1)
    family.attrs.create("NBR", len(points))

    # For point tags
    node = families.create_group("NOEUD")
    _write_families(node, tags)


def _resolve_element_in_use_by_other_set(tagged_data, ind, tags, name, is_elem):
    existing_id = int(tagged_data[ind])
    current_tags = tags[existing_id]
    all_tags = current_tags + [name]

    if name in current_tags:
        logger.error("Unexpected error. Name already exists in set during resolving set members.")

    new_int = None
    for i_, t_ in tags.items():
        if all_tags == t_:
            new_int = i_
            break

    if new_int is None:
        new_int = int(min(tags.keys()) - 1) if is_elem else int(max(tags.keys()) + 1)
        tags[new_int] = tags[existing_id] + [name]

    tagged_data[ind] = new_int


def _set_to_tags(sets, data, tag_start_int, tags, id_map=None):
    """

    :param sets:
    :param data:
    :param tag_start_int:
    :param
    :return: The tagged data.
    """
    tagged_data = np.zeros(len(data), dtype=np.int32)
    tag_int = 0 + tag_start_int

    is_elem = False if tag_int > 0 else True

    tag_int = tag_start_int
    tag_map = dict()
    # Generate basic tags upfront
    for name in sets.keys():
        tags[tag_int] = [name]
        tag_map[name] = tag_int
        if is_elem is True:
            tag_int -= 1
        else:
            tag_int += 1

    for name, set_data in sets.items():
        if len(set_data) == 0:
            continue

        for index_ in set_data:
            index = int(index_ - 1)

            if id_map is not None:
                index = id_map[index_]

            if index > len(tagged_data) - 1:
                raise IndexError()

            if tagged_data[index] != 0:  # id is already defined in another set
                _resolve_element_in_use_by_other_set(tagged_data, index, tags, name, is_elem)
            else:
                tagged_data[index] = tag_map[name]

    return tagged_data


def _family_name(set_id, name):
    """Return the FAM object name corresponding to the unique set id and a list of subset names"""
    return "FAM" + "_" + str(set_id) + "_" + "_".join(name)


def _write_families(fm_group, tags):
    """Write point/cell tag information under FAS/[mesh_name]"""
    for set_id, name in tags.items():
        family = fm_group.create_group(_family_name(set_id, name))
        family.attrs.create("NUM", set_id)
        group = family.create_group("GRO")
        group.attrs.create("NBR", len(name))  # number of subsets
        dataset = group.create_dataset("NOM", (len(name),), dtype="80int8")
        for i in range(len(name)):
            # make name 80 characters
            name_80 = name[i] + "\x00" * (80 - len(name[i]))
            # Needs numpy array, see <https://github.com/h5py/h5py/issues/1735>
            dataset[i] = np.array([ord(x) for x in name_80])
