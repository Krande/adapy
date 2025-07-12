from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import numpy as np
import trimesh

from ada.config import logger
from ada.core.guid import create_guid
from ada.extension import simulation_extension_schema as sim_meta
from ada.extension.simulation_extension_schema import FeObjectType, SimNodeReference
from ada.visit.gltf.graph import GraphNode, GraphStore

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult
    from ada.visit.scene_converter import SceneConverter


def scene_from_fem_results(fea_res: FEAResult, converter: SceneConverter):
    from trimesh.path.entities import Line

    from ada.api.animations import Animation
    from ada.core.vector_transforms import rot_matrix
    from ada.fem.results.field_data import ElementFieldData, NodalFieldData

    params = converter.params

    warp_scale = params.fea_params.warp_scale

    # initial mesh
    vertices = fea_res.mesh.nodes.coords
    # node_ids = fea_res.mesh.nodes.identifiers

    edges, faces = fea_res.mesh.get_edges_and_faces_from_mesh()

    # faces
    faces_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)

    # edges
    entities = [Line(x) for x in edges]
    edge_mesh = trimesh.path.Path3D(entities=entities, vertices=vertices)

    # points
    points_mesh = trimesh.points.PointCloud(vertices=vertices)

    scene = trimesh.Scene()

    face_node = scene.add_geometry(faces_mesh, node_name=fea_res.name, geom_name="faces")
    edge_node = scene.add_geometry(
        edge_mesh, node_name=f"{fea_res.name}_edges", geom_name="edges", parent_node_name=fea_res.name
    )
    points_node = scene.add_geometry(points_mesh, node_name=f"{fea_res.name}_points", geom_name="points")

    face_node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == face_node][0]

    # React renderer supports animations
    sim_data = export_sim_metadata(fea_res)
    sim_data.node_references = SimNodeReference(faces=face_node, edges=edge_node, points=points_node)

    groups = []
    if fea_res.mesh.sets is not None:
        for fset in fea_res.mesh.sets.values():
            ftype = FeObjectType.node if fset.type == fset.TYPES.NSET else FeObjectType.element
            g = sim_meta.SimGroup(
                name=fset.name,
                members=[f"EL{m}" if fset.type == fset.TYPES.ELSET else f"P{m}" for m in fset.members],
                parent_name=sim_data.name,
                description=fset.type,
                fe_object_type=ftype,
            )
            groups.append(g)
        sim_data.groups = groups

    converter.ada_ext.simulation_objects.append(sim_data)

    # Loop over the results and create an animation from it
    vertices = fea_res.mesh.nodes.coords
    added_results = []
    for i, result in enumerate(fea_res.results):
        if isinstance(result, ElementFieldData):
            continue
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
        converter.add_animation(animation)

    if params.apply_transform:
        # if you want Y is up
        m3x3 = rot_matrix((0, -1, 0))
        m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
        m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
        scene.apply_transform(m4x4)

    params.set_gltf_buffer_postprocessor(converter.buffer_postprocessor)
    params.set_gltf_tree_postprocessor(converter.tree_postprocessor)

    parent_node = GraphNode("world", 0, hash=create_guid())
    graph = GraphStore(top_level=parent_node, nodes={0: parent_node})
    graph.add_node(GraphNode(fea_res.name, graph.next_node_id(), hash=create_guid(), parent=parent_node))
    scene.metadata.update(graph.create_meta())

    return scene


def export_sim_metadata(fea_res: FEAResult) -> sim_meta.SimulationDataExtensionMetadata:
    steps = []
    for x in fea_res.results:
        if x.step not in steps:
            steps.append(x.step)

    step_objects = []
    for step in steps:
        fields = []
        is_eig = False
        for result in fea_res.results:
            if result.step != step:
                continue
            if hasattr(result, "eigen_freq") and result.eigen_freq is not None:
                is_eig = True
            field = sim_meta.FieldObject(
                name=result.name,
                type=result.field_type.value if hasattr(result, "field_type") else "unknown",
                data=sim_meta.DataReference(bufferView=0, byteOffset=0),
            )
            fields.append(field)

        if is_eig:
            analysis_type = sim_meta.AnalysisType.eigenvalue
        else:
            analysis_type = sim_meta.AnalysisType.implicit_static

        step_objects.append(sim_meta.StepObject(analysis_type=analysis_type, fields=fields))

    # Get the last modified date of the result file
    last_modified = datetime.datetime.fromtimestamp(fea_res.results_file_path.stat().st_mtime)
    logger.info(f"Last modified date of the result file: {last_modified}")

    return sim_meta.SimulationDataExtensionMetadata(
        name=fea_res.name,
        date=last_modified,
        fea_software=str(fea_res.software.value),
        fea_software_version=fea_res.software_version,
        steps=step_objects,
    )
