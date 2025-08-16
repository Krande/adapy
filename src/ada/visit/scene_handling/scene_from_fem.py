from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from ada.base.physical_objects import BackendGeom
from ada.fem import Elem
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

    params = converter.params
    graph = converter.graph

    if fem.parent is not None:
        parent_part_node = graph.hash_map.get(fem.parent.guid)
    else:
        parent_part_node = graph.top_level

    parent_node = graph.add_node(GraphNode(fem.name, graph.next_node_id(), parent=parent_part_node))

    use_solid_beams = params.fea_params is not None and params.fea_params.solid_beams is True

    ms = fem.to_mesh().create_mesh_stores(
        fem.name,
        graph,
        parent_node,
        use_solid_beams=use_solid_beams,
    )

    scene = trimesh.Scene(base_frame=graph.top_level.name) if converter.scene is None else converter.scene

    ms.add_to_scene(scene, graph)

    groups = []
    for fset in fem.sets.sets:
        ftype = FeObjectType.node if fset.type == fset.TYPES.NSET else FeObjectType.element
        members = []

        # Note! The node/elem id are not the right reference iD's, the id needs to refer to the mesh node/elem id.
        for m in fset.members:
            if isinstance(m, (Elem, Node)):
                name = m.id
            elif isinstance(m, BackendGeom):
                name = m.name
            else:
                raise ValueError(f"Unsupported type of set member: {type(m)}")

            if fset.type == fset.TYPES.NSET:
                members.append(f"P{name}")
            else:
                elem_ref = f"EL{name}"
                members.append(elem_ref)

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
            points=ms.points_node_name,
            edges=ms.edges_node_name,
            faces=ms.faces_node_name,
            solid_beams=ms.bm_solid_node_name,
        ),
        groups=groups,
    )
    converter.ada_ext.simulation_objects.append(sim_data)

    return scene
