from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Union

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.utils import convert_obj_to_poly

if TYPE_CHECKING:
    from ada import Beam, Plate


def merge_objects_into_single_json(guid, colour, opacity, list_of_objects: Iterable[Union[Beam, Plate]]) -> dict:
    id_sequence = dict()
    indices = np.array([], dtype=int)
    position = np.array([], dtype=float)
    for obj in list_of_objects:
        res = convert_obj_to_poly(obj)
        pos_len = int(len(position) / 6)
        new_index = np.array(res["index"]) + pos_len
        mi, ma = min(new_index), max(new_index)
        position = np.concatenate([np.array(position), res["position"]])
        indices = np.concatenate([indices, new_index])
        id_sequence[obj.guid] = (int(mi), int(ma))

    return dict(
        guid=guid,
        index=indices.astype(int).tolist(),
        position=position.flatten().astype(float).tolist(),
        color=[*colour, opacity],
        instances=[],
        id_sequence=id_sequence,
    )


def merge_by_colours(name, list_of_objects: Iterable[Union[Beam, Plate]]):
    colour_map: Dict[str, List[Union[Beam, Plate]]] = dict()
    for obj in list_of_objects:
        if obj.colour not in colour_map.keys():
            colour_map[obj.colour] = []

        colour_map[obj.colour].append(obj)

    id_map = dict()

    for colour, elements in colour_map.items():
        el0 = elements[0]
        guid = create_guid()
        id_map[guid] = merge_objects_into_single_json(el0.guid, el0.colour_norm, el0.opacity, elements)

    merged_part = {
        "name": name,
        "rawdata": True,
        "guiParam": None,
        "treemeta": {},
        "id_map": id_map,
        "meta": "url til json",
    }
    return [merged_part]
