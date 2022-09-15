from __future__ import annotations

from typing import Dict, Tuple

import ada
from ada.visualize.config import ExportConfig


def generate_meta(part: ada.Part, export_config: ExportConfig, sub_geometries: Dict[str, Tuple[str, str]] = None):
    meta = dict()
    for obj in part.get_all_physical_objects(
        sub_elements_only=False,
        filter_by_guids=export_config.data_filter.filter_elements_by_guid,
    ):
        meta[obj.guid] = (obj.name, obj.parent.guid)
        if export_config.data_filter.name_filter is not None and len(export_config.data_filter.name_filter) > 0:
            if obj.name not in [fi.lower() for fi in export_config.data_filter.name_filter]:
                continue
    # Add additional refs to sub-geometry elements
    for key, value in sub_geometries.items():
        meta[key] = value

    for p in part.get_all_parts_in_assembly(True):
        parent_id = p.parent.guid if p.parent is not None else None
        if isinstance(p.parent, ada.Assembly):
            parent_id = "*"
        meta[p.guid] = (p.name, parent_id)

    return meta
