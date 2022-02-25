from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Tuple, Union

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.concept import PolyModel
from ada.visualize.formats.custom_json.write_objects_to_json import obj_to_json

from .config import ExportConfig

if TYPE_CHECKING:
    from ada import Beam, Plate


def merge_objects_into_single_json(
    guid,
    list_of_objects: Iterable[Union[Beam, Plate]],
    export_config,
    obj_num,
    all_num,
) -> Tuple[PolyModel, int]:

    pm = PolyModel(
        guid,
        np.array([], dtype=int),
        np.array([], dtype=float),
        np.array([], dtype=float),
    )

    for obj in list_of_objects:
        print(f"Converting {obj_num} of {all_num} to PolyModel")
        res = obj_to_json(obj, export_config=export_config)
        if res is None:
            continue
        pm += res
        obj_num += 1

    return pm, obj_num


def merge_by_colours(
    name, list_of_objects: Iterable[Union[Beam, Plate]], export_config: ExportConfig, obj_num, all_obj_num
):
    colour_map: Dict[str, List[Union[Beam, Plate]]] = dict()

    for obj in list_of_objects:
        if obj.colour not in colour_map.keys():
            colour_map[obj.colour] = []

        colour_map[obj.colour].append(obj)

    id_map = dict()
    for colour, elements in colour_map.items():
        guid = create_guid()
        pm, obj_num = merge_objects_into_single_json(
            guid,
            elements,
            export_config,
            obj_num,
            all_obj_num,
        )
        id_map[guid] = pm.to_dict()

    merged_part = {
        "name": name,
        "rawdata": True,
        "guiParam": None,
        "treemeta": {},
        "id_map": id_map,
        "meta": "url til json",
    }
    return [merged_part]
