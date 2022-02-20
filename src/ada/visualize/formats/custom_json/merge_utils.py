from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Union

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.formats.custom_json.write_objects_to_json import obj_to_json

if TYPE_CHECKING:
    from ada import Beam, Plate


def merge_objects_into_single_json(colour, opacity, list_of_objects: Iterable[Union[Beam, Plate]]) -> dict:
    id_sequence = dict()
    indices = np.array([], dtype=int)
    position = np.array([], dtype=float)
    normals = np.array([], dtype=float)

    for obj in list_of_objects:
        res = obj_to_json(obj)
        pos_len = int(len(position) / 3)
        new_index = np.array(res["index"]) + pos_len
        mi, ma = min(new_index), max(new_index)
        position = np.concatenate([position, np.array(res["position"])])
        normals = np.concatenate([position, np.array(res["normal"])])
        indices = np.concatenate([indices, new_index])
        id_sequence[obj.guid] = (int(mi), int(ma))

    return dict(
        index=indices.astype(int).tolist(),
        position=position.flatten().astype(float).tolist(),
        normal=normals.flatten().astype(float).tolist(),
        color=[*colour, opacity],
        vertexColor=None,
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
        id_map[guid] = merge_objects_into_single_json(el0.colour_norm, el0.opacity, elements)

    merged_part = {
        "name": name,
        "rawdata": True,
        "guiParam": None,
        "treemeta": {},
        "id_map": id_map,
        "meta": "url til json",
    }
    return [merged_part]
