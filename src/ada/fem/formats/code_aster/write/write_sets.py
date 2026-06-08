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

    # Materialise the element groups + per-group id→index maps once
    # up front. The old loop re-filtered every res_data member by the
    # current ``group`` on each outer iteration — O(G·M·N) where G is
    # the number of element-type groups, M the number of tags, N the
    # members per tag. On large FEM → MED conversions that quadratic-
    # ish term dominated the hot path. Single pre-pass bucketing
    # collapses it to O(total members) overall.
    groups: list = []
    elements_by_group: dict = {}
    cell_ids_by_group: dict = {}
    for group, elements in part.fem.elements.group_by_type():
        elements_list = list(elements)
        groups.append(group)
        elements_by_group[group] = elements_list
        cell_ids_by_group[group] = {el.id: i for i, el in enumerate(elements_list)}

    # Per-group sparse assignment: ``index_in_group → tag_int``. Built
    # by a single walk over res_data members instead of a nested
    # filter per group.
    pending: dict = {g: [] for g in groups}  # list[tuple[index, tag]]
    for t, mem in res_data.items():
        for el in mem:
            cmap = cell_ids_by_group.get(el.type)
            if cmap is None:
                continue
            idx = cmap.get(el.id)
            if idx is not None:
                pending[el.type].append((idx, t))

    for group in groups:
        elements_list = elements_by_group[group]
        cell_data = np.zeros(len(elements_list), dtype=np.int32)
        for idx, t in pending[group]:
            cell_data[idx] = t

        if isinstance(group, (shape_def.MassTypes, shape_def.SpringTypes)):
            # Mirror the ``.members`` → ``.nodes`` fallback in
            # :mod:`write_med` — Mass/Spring proper subclasses populate
            # ``.members`` via ``fem_set`` assignment; cross-format
            # readers emit plain ``Elem`` instances that only carry
            # ``.nodes``. Single helper handles both.
            from .write_med import _mass_or_spring_attach_id

            cells = np.array([_mass_or_spring_attach_id(el) for el in elements_list])
        else:
            cells = np.array(list(map(get_node_ids_from_element, elements_list)))

        med_type = ada_to_med_type(group, part.fem.options.CODE_ASTER.use_reduced_integration)
        med_cells = cells_group.get(med_type)
        family = med_cells.create_dataset("FAM", data=cell_data)
        family.attrs.create("CGT", 1)
        family.attrs.create("NBR", len(cells))

    _write_families(element, tags)


def _add_node_sets(nodes_group, fems, points, families):
    """Write the node-set family table ("FAM") for one or more FEMs into ``nodes_group``.

    Takes a list of FEMs (the part's plus, when distinct, the assembly's) and merges their
    nsets so the single MED "FAM" dataset is created exactly once — creating it per-FEM
    raised "dataset name already exists" for decks with both part- and assembly-level
    node sets."""
    tags = dict()
    nsets = dict()
    for fem in fems:
        for key, val in fem.nsets.items():
            # Read member ids straight off the id-backed set — resolving each id to a Node proxy
            # only to read .id back is pure overhead (and was O(N^2) via FemSet iteration).
            mids = getattr(val, "_member_ids", None)
            nsets[key] = [int(i) for i in mids] if mids is not None else [int(p.id) for p in val.members]

    points = _set_to_tags(nsets, points, 2, tags)

    family = nodes_group.create_dataset("FAM", data=points)
    family.attrs.create("CGT", 1)
    family.attrs.create("NBR", len(points))

    # For point tags
    node = families.create_group("NOEUD")
    _write_families(node, tags)


def _set_to_tags(sets, data, tag_start_int, tags, id_map=None):
    """Tag each datum (node/element) with the family id for the combination of sets it belongs
    to, allocating a new combined family the first time a given combination appears.

    Combination lookup is O(1) via ``rmap`` (combination tuple -> family id) and new family ids
    come from running min/max counters — mirroring :func:`resolve_ids_in_multiple` on the cell
    side. The earlier version linear-scanned ``tags`` and re-aggregated ``min/max`` per
    overlapping member, which went quadratic on decks with large overlapping node sets (e.g. a
    merged multi-instance model), dominating the MED write.

    :return: The tagged data.
    """
    tagged_data = np.zeros(len(data), dtype=np.int32)
    is_elem = tag_start_int <= 0

    tag_int = tag_start_int
    tag_map = dict()
    # Generate basic (single-set) tags upfront
    for name in sets.keys():
        tags[tag_int] = [name]
        tag_map[name] = tag_int
        tag_int += -1 if is_elem else 1

    # O(1) combination lookup + running extents for new-family allocation.
    rmap = {tuple(v): r for r, v in tags.items()}
    next_neg = (min(tags.keys()) - 1) if tags else -1
    next_pos = (max(tags.keys()) + 1) if tags else 1

    for name, set_data in sets.items():
        if len(set_data) == 0:
            continue

        for index_ in set_data:
            index = id_map[index_] if id_map is not None else int(index_ - 1)

            if index > len(tagged_data) - 1:
                raise IndexError()

            existing = int(tagged_data[index])
            if existing == 0:  # first set to claim this datum
                tagged_data[index] = tag_map[name]
                continue

            # Already in another set -> family for the combined membership.
            current_tags = tags[existing]
            if name in current_tags:
                logger.error("Unexpected error. Name already exists in set during resolving set members.")
            combo = current_tags + [name]
            key = tuple(combo)
            new_int = rmap.get(key)
            if new_int is None:
                if is_elem:
                    new_int = next_neg
                    next_neg -= 1
                else:
                    new_int = next_pos
                    next_pos += 1
                tags[new_int] = combo
                rmap[key] = new_int
            tagged_data[index] = new_int

    return tagged_data


_MED_NAME_SIZE = 64  # libmed MED_NAME_SIZE — family group name must fit in this many bytes


def _family_name(set_id, name):
    """Return the FAM object name corresponding to the unique set id and a list of subset names.

    Defensive: dedupes the name list (preserving order) and truncates
    the joined result so the full group name stays under libmed's
    ``MED_NAME_SIZE`` cap. The leading ``FAM_<id>_`` already guarantees
    uniqueness via the numeric id, so truncating the joined names
    portion can't cause name collisions.
    """
    deduped = list(dict.fromkeys(name))
    prefix = "FAM_" + str(set_id) + "_"
    budget = _MED_NAME_SIZE - len(prefix)
    joined = "_".join(deduped)
    if len(joined) > budget:
        joined = joined[:budget]
    return prefix + joined


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
