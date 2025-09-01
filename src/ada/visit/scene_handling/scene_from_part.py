from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import trimesh

    from ada import Assembly, Part
    from ada.visit.scene_converter import SceneConverter


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, converter: SceneConverter) -> trimesh.Scene:
    import ada.extension.design_extension_schema as design_ext
    from ada import Assembly
    from ada.config import logger
    from ada.occ.tessellating import BatchTessellator

    params = converter.params

    if params.stream_from_ifc_store and params.auto_sync_ifc_store and isinstance(part_or_assembly, Assembly):
        part_or_assembly.ifc_store.sync()

    bt = BatchTessellator()

    graph = converter.graph
    graph.add_nodes_from_part(part_or_assembly)

    groups = []

    for group_name, groups_ in part_or_assembly.get_all_groups_as_merged().items():
        group0 = groups_[0]
        members = [m.name for g in groups_ for m in g.members]
        g = design_ext.Group(
            name=group_name, members=members, description=group0.description, parent_name=part_or_assembly.name
        )
        groups.append(g)

    scene = None
    if params.stream_from_ifc_store:
        if isinstance(part_or_assembly, Assembly):
            scene = bt.ifc_to_trimesh_scene(
                part_or_assembly.get_assembly().ifc_store, merge_meshes=params.merge_meshes, graph=graph
            )
        else:
            logger.warning(
                "Stream from ifc store is only supported from Assembly objects, not Part objects. "
                "Will use default tessellation using pythonocc-core"
            )
    if scene is None:
        scene = bt.tessellate_part(part_or_assembly, params=params, graph=graph)

    nodes_geom = set(scene.graph.nodes_geometry)
    converter.ada_ext.design_objects.append(
        design_ext.DesignDataExtension(
            name=part_or_assembly.name,
            description=type(part_or_assembly).__name__,
            groups=groups,
            node_references=design_ext.DesignNodeReference(faces=list(nodes_geom)),
        )
    )

    return scene
