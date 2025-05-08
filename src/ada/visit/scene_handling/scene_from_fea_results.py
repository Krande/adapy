from __future__ import annotations

from typing import TYPE_CHECKING

import datetime

import numpy as np
import trimesh

from ada.core.guid import create_guid
from ada.fem import sim_metadata as sim_meta
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.render_params import RenderParams

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

def scene_from_fem_results(fea_res: FEAResult, params: RenderParams):
    from trimesh.path.entities import Line

    from ada.api.animations import Animation
    from ada.visit.gltf.gltf_postprocessor import GltfPostProcessor
    from ada.core.vector_transforms import rot_matrix
    from ada.fem.results.field_data import NodalFieldData


    warp_scale = params.fea_params.warp_scale

    # initial mesh
    vertices = fea_res.mesh.nodes.coords
    edges, faces = fea_res.mesh.get_edges_and_faces_from_mesh()

    faces_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)

    entities = [Line(x) for x in edges]
    edge_mesh = trimesh.path.Path3D(entities=entities, vertices=vertices)

    scene = trimesh.Scene()
    face_node = scene.add_geometry(faces_mesh, node_name=fea_res.name, geom_name="faces")
    _ = scene.add_geometry(
        edge_mesh, node_name=f"{fea_res.name}_edges", geom_name="edges", parent_node_name=fea_res.name
    )

    face_node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == face_node][0]
    # edge_node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == edge_node][0]

    # React renderer supports animations
    sim_data = export_sim_metadata(fea_res)
    gltf_postprocessor = GltfPostProcessor(extensions={"ADA_SIM_data": sim_data.model_dump()})

    # Loop over the results and create an animation from it
    vertices = fea_res.mesh.nodes.coords
    added_results = []
    for i, result in enumerate(fea_res.results):
        warped_vertices = fea_res._warp_data(vertices, result.name, result.step, warp_scale)
        delta_vertices = warped_vertices - vertices
        is_static = False
        if isinstance(result, NodalFieldData):
            is_static = False if result.eigen_freq is not None else True
        if is_static:
            time_steps = [0, 2]
            weight_steps = [0, 1]
        else:
            time_steps = [0, 2, 4, 6, 8]
            weight_steps = [0, 1, 0, -1, 0]
        result_name = f"{result.name}_{result.step}"
        if result_name in added_results:
            result_name = f"{result.name}_{result.step}_{i}"
        added_results.append(result_name)
        animation = Animation(
            result_name,
            time_steps,
            deformation_weights_keyframes=weight_steps,
            deformation_shape=delta_vertices,
            node_idx=[face_node_idx],
        )
        gltf_postprocessor.add_animation(animation)

    if params.apply_transform:
        # if you want Y is up
        m3x3 = rot_matrix((0, -1, 0))
        m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
        m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
        scene.apply_transform(m4x4)

    params.gltf_buffer_postprocessor = gltf_postprocessor.buffer_postprocessor
    params.gltf_tree_postprocessor = gltf_postprocessor.tree_postprocessor

    parent_node = GraphNode("world", 0, hash=create_guid())
    graph = GraphStore(top_level=parent_node, nodes={0: parent_node})
    graph.add_node(GraphNode(fea_res.name, graph.next_node_id(), hash=create_guid(), parent=parent_node))
    scene.metadata.update(graph.create_meta())
    return scene


def export_sim_metadata(fea_res: FEAResult)-> sim_meta.SimulationDataExtensionMetadata:
    steps = []
    for x in fea_res.results:
        if x.step not in steps:
            steps.append(x.step)

    step_objects = []
    fields = []
    for result in fea_res.results:
        fields.append(
            sim_meta.FieldObject(
                name=result.name,
                type=result.field_type.value,
                data=sim_meta.DataReference(bufferView=0, byteOffset=0),
            )
        )
        step_objects.append(sim_meta.StepObject(analysis_type=sim_meta.AnalysisType.eigenvalue.value, fields=fields))

    return sim_meta.SimulationDataExtensionMetadata(
        name=fea_res.name,
        date=datetime.datetime.now(),
        fea_software=fea_res.software,
        fea_software_version=fea_res.software,
        steps=step_objects,
    )
