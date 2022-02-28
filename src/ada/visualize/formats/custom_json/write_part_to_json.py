from __future__ import annotations

from typing import TYPE_CHECKING

from .config import ExportConfig

if TYPE_CHECKING:
    from ada import Part


def export_part_to_json(part: "Part", export_config: ExportConfig) -> dict:
    all_obj_num = len(
        list(
            part.get_all_physical_objects(
                sub_elements_only=False,
                filter_by_guids=export_config.filter_elements_by_guid,
            )
        )
    )

    print(f"Exporting {all_obj_num} physical objects to custom json format.")
    obj_num = 1

    if export_config.merge_by_colour is True:
        from .merge_utils import merge_by_colours

        part_array = merge_by_colours(
            part.name,
            part.get_all_physical_objects(filter_by_guids=export_config.filter_elements_by_guid),
            export_config,
            obj_num,
            all_obj_num,
        )
    else:
        part_array = []
        for p in [part, *part.get_all_subparts()]:
            pjson = part_to_json_values(p, export_config, obj_num, all_obj_num)
            part_array.append(pjson)

    output = {
        "name": part.name,
        "created": "dato",
        "project": part.metadata.get("project", "DummyProject"),
        "world": part_array,
    }
    return output


def part_to_json_values(p: "Part", export_config: ExportConfig, obj_num, all_obj_num) -> dict:

    from .write_objects_to_json import id_map_using_threading, list_of_obj_to_json

    if export_config.threads != 1:
        id_map = id_map_using_threading(list(p.get_all_physical_objects()), export_config.threads)
    else:
        id_map = list_of_obj_to_json(p.get_all_physical_objects(), obj_num, all_obj_num, export_config)

    for inst in p.instances.values():
        id_map[inst.instance_ref.guid]["instances"] = inst.to_list_of_custom_json_matrices()

    return {
        "name": p.name,
        "rawdata": True,
        "guiParam": None,
        "treemeta": {},
        "id_map": id_map,
        "meta": "url til json",
    }
