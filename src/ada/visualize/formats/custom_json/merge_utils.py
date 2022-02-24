from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Union

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.concept import PolyModel
from ada.visualize.formats.custom_json.write_objects_to_json import obj_to_json

from .config import ExportConfig

if TYPE_CHECKING:
    from ada import Beam, Plate


def merge_objects_into_single_json(
    guid, colour, opacity, list_of_objects: Iterable[Union[Beam, Plate]], export_config: ExportConfig
) -> PolyModel:
    pm = PolyModel(
        guid, np.array([], dtype=int), np.array([], dtype=float), np.array([], dtype=float), [*colour, opacity]
    )

    for i, obj in enumerate(list_of_objects):
        print(f"Converting {i} of {len(list(list_of_objects))} to PolyModel")
        if export_config.filter_elements_by_guid is not None and obj.guid not in export_config.filter_elements_by_guid:
            continue
        res = obj_to_json(obj)
        if res is None:
            continue
        pm += res

    return pm


def merge_by_colours(name, list_of_objects: Iterable[Union[Beam, Plate]], export_config: ExportConfig):
    colour_map: Dict[str, List[Union[Beam, Plate]]] = dict()

    for obj in list_of_objects:
        if obj.colour not in colour_map.keys():
            colour_map[obj.colour] = []

        colour_map[obj.colour].append(obj)

    id_map = dict()

    for colour, elements in colour_map.items():
        el0 = elements[0]
        guid = create_guid()
        id_map[guid] = merge_objects_into_single_json(
            guid, el0.colour_norm, el0.opacity, elements, export_config
        ).to_dict()

    merged_part = {
        "name": name,
        "rawdata": True,
        "guiParam": None,
        "treemeta": {},
        "id_map": id_map,
        "meta": "url til json",
    }
    return [merged_part]
