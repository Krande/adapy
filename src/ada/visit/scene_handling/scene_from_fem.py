from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from ada.core.guid import create_guid
from ada.visit.colors import Color
from ada.visit.gltf.graph import GraphNode

if TYPE_CHECKING:
    import trimesh

    from ada import FEM
    from ada.visit.scene_converter import SceneConverter


def scene_from_fem(fem: FEM, converter: SceneConverter) -> trimesh.Scene:
    """Appends a FE mesh to scene or creates a new scene if no scene is provided."""

    import trimesh

    from ada import Node
    from ada.extension import simulation_extension_schema as sim_meta
    from ada.extension.simulation_extension_schema import FeObjectType
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    params = converter.params
    graph = converter.graph

    if fem.parent is not None:
        parent_part_node = graph.hash_map.get(fem.parent.guid)
    else:
        parent_part_node = graph.top_level

    parent_node = graph.add_node(GraphNode(fem.name, graph.next_node_id(), parent=parent_part_node))

    shell_color = Color.from_str("white")
    shell_color_id = graph.next_node_id()
    line_color = Color.from_str("gray")
    line_color_id = graph.next_node_id() + 1
    points_color = Color.from_str("black")
    points_color_id = graph.next_node_id() + 2
    solid_bm_color = Color.from_str("light-gray")
    solid_bm_color_id = graph.next_node_id() + 3

    use_solid_beams = params.fea_params is not None and params.fea_params.solid_beams is True

    mesh = fem.to_mesh()
    points_store, edge_store, face_store = mesh.create_mesh_stores(
        fem.name,
        shell_color,
        line_color,
        points_color,
        graph,
        parent_node,
        use_solid_beams=use_solid_beams,
    )

    scene = trimesh.Scene(base_frame=graph.top_level.name) if converter.scene is None else converter.scene
    line_elems = list(fem.elements.lines)

    bm_solid_node_name = None
    if use_solid_beams and len(line_elems) > 0:
        from ada.fem.formats.utils import line_elem_to_beam
        from ada.occ.tessellating import BatchTessellator
        from ada.visit.gltf.optimize import concatenate_stores

        so_bm_node = graph.add_node(
            GraphNode(fem.name + "_liSO", graph.next_node_id(), hash=create_guid(), parent=parent_node)
        )
        beams = [line_elem_to_beam(elem, fem.parent, "BM") for elem in fem.elements.lines]
        for bm in beams:
            graph.add_node(GraphNode(bm.name, graph.next_node_id(), hash=bm.guid, parent=so_bm_node))

        bt = BatchTessellator()
        meshes = bt.batch_tessellate(beams, graph_store=graph)
        merged_store = concatenate_stores(meshes)

        bm_solid_node_name = merged_mesh_to_trimesh_scene(
            scene, merged_store, solid_bm_color, solid_bm_color_id, graph_store=graph
        )

    edges_node_name = None
    if len(edge_store.indices) > 0:
        edges_node_name = merged_mesh_to_trimesh_scene(scene, edge_store, line_color, line_color_id, graph_store=graph)

    faces_node_name = None
    if len(face_store.indices) > 0:
        faces_node_name = merged_mesh_to_trimesh_scene(
            scene, face_store, shell_color, shell_color_id, graph_store=graph
        )

    points_node_name = None
    if len(points_store.position) > 0:
        points_node_name = merged_mesh_to_trimesh_scene(
            scene, points_store, points_color, points_color_id, graph_store=graph
        )

    groups = []
    for fset in fem.sets.sets:
        ftype = FeObjectType.node if fset.type == fset.TYPES.NSET else FeObjectType.element
        members = []
        # Note! The node/elem id are not the right reference iD's, the id needs to refer to the mesh node/elem id.
        for m in fset.members:
            if hasattr(m, "name"):
                name = m.name
            else:
                if isinstance(m, Node):
                    name = m.id
                else:
                    raise ValueError(f"Unsupported type of set member: {type(m)}")

            if fset.type == fset.TYPES.NSET:
                members.append(f"P{name}")
            else:
                members.append(f"EL{name}")

        g = sim_meta.SimGroup(
            name=fset.name,
            members=members,
            parent_name=fem.name,
            description=fset.type,
            fe_object_type=ftype,
        )
        groups.append(g)

    sim_data = sim_meta.SimulationDataExtensionMetadata(
        name=fem.name,
        date=datetime.datetime.now(),
        fea_software="N/A",
        fea_software_version="N/A",
        steps=[],
        node_references=sim_meta.SimNodeReference(
            points=points_node_name, edges=edges_node_name, faces=faces_node_name, solid_beams=bm_solid_node_name
        ),
        groups=groups,
    )
    converter.ada_ext.simulation_objects.append(sim_data)

    return scene
