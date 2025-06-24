from __future__ import annotations

from typing import TYPE_CHECKING

import trimesh

from ada.visit.render_params import RenderParams


if TYPE_CHECKING:
    from ada import Assembly, Part
    from ada.visit.scene_converter import SceneConverter


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, converter: SceneConverter) -> trimesh.Scene:
    from ada import Assembly
    from ada.occ.tessellating import BatchTessellator

    params = converter.params

    if params.stream_from_ifc_store and params.auto_sync_ifc_store and isinstance(part_or_assembly, Assembly):
        part_or_assembly.ifc_store.sync()

    bt = BatchTessellator()

    if params.stream_from_ifc_store and isinstance(part_or_assembly, Assembly):
        scene = bt.ifc_to_trimesh_scene(part_or_assembly.get_assembly().ifc_store, merge_meshes=params.merge_meshes)
    else:
        scene = bt.tessellate_part(part_or_assembly, params=params)

    return scene
