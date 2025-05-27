from __future__ import annotations

from typing import TYPE_CHECKING

import trimesh

from ada.visit.render_params import RenderParams

if TYPE_CHECKING:
    from ada import Assembly, Part


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, params: RenderParams) -> trimesh.Scene:
    from ada import Assembly

    if params.stream_from_ifc_store and params.auto_sync_ifc_store and isinstance(part_or_assembly, Assembly):
        part_or_assembly.ifc_store.sync()

    scene = part_or_assembly.to_trimesh_scene(
        stream_from_ifc=params.stream_from_ifc_store,
        merge_meshes=params.merge_meshes,
        params=params,
    )
    return scene
