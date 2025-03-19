from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, Optional, OrderedDict

import numpy as np
import trimesh

from ada.comms.fb_wrap_model_gen import (
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MeshDC,
    SceneDC,
    SceneOperationsDC,
)
from ada.config import Config
from ada.core.guid import create_guid
from ada.visit.colors import Color
from ada.visit.gltf.graph import GraphNode, GraphStore

if TYPE_CHECKING:
    from IPython.display import HTML

    from ada import FEM, Assembly, Part
    from ada.base.physical_objects import BackendGeom
    from ada.fem.results.common import FEAResult


@dataclass
class FEARenderParams:
    step: int = (None,)
    field: str = (None,)
    warp_field: str = (None,)
    warp_step: int = (None,)
    cfunc: Callable[[list[float]], float] = (None,)
    warp_scale: float = 1.0
    solid_beams: bool = False


@dataclass
class RenderParams:
    auto_sync_ifc_store: bool = True
    stream_from_ifc_store: bool = True
    merge_meshes: bool = True
    scene_post_processor: Optional[Callable[[trimesh.Scene], trimesh.Scene]] = None
    purpose: Optional[FilePurposeDC] = FilePurposeDC.DESIGN
    scene: SceneDC = None
    gltf_buffer_postprocessor: Optional[Callable[[OrderedDict, dict], None]] = None
    gltf_tree_postprocessor: Optional[Callable[[OrderedDict], None]] = None
    gltf_export_to_file: str | pathlib.Path = None
    gltf_asset_extras_dict: dict = None
    add_ifc_backend: bool = False
    backend_file_dir: Optional[str] = None
    unique_id: int = None
    fea_params: Optional[FEARenderParams] = field(default_factory=FEARenderParams)

    def __post_init__(self):
        # ensure that if unique_id is set, it is a 32-bit integer
        if self.unique_id is not None:
            self.unique_id = self.unique_id & 0xFFFFFFFF
        if self.scene is None:
            self.scene = SceneDC(operation=SceneOperationsDC.REPLACE)


def scene_from_fem_results(fea_res: FEAResult, params: RenderParams):
    from trimesh.path.entities import Line

    from ada.api.animations import Animation, AnimationStore
    from ada.core.vector_transforms import rot_matrix

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
    animation_store = AnimationStore()

    # Loop over the results and create an animation from it
    vertices = fea_res.mesh.nodes.coords
    added_results = []
    for i, result in enumerate(fea_res.results):
        warped_vertices = fea_res._warp_data(vertices, result.name, result.step, warp_scale)
        delta_vertices = warped_vertices - vertices
        result_name = f"{result.name}_{result.step}"
        if result_name in added_results:
            result_name = f"{result.name}_{result.step}_{i}"
        added_results.append(result_name)
        animation = Animation(
            result_name,
            [0, 2, 4, 6, 8],
            deformation_weights_keyframes=[0, 1, 0, -1, 0],
            deformation_shape=delta_vertices,
            node_idx=[face_node_idx],
        )
        animation_store.add(animation)

    # Trimesh automatically transforms by setting up = Y. This will counteract that transform
    m3x3 = rot_matrix((0, -1, 0))
    m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
    m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
    scene.apply_transform(m4x4)

    params.gltf_buffer_postprocessor = animation_store
    params.gltf_tree_postprocessor = AnimationStore.tree_postprocessor
    parent_node = GraphNode("world", 0, hash=create_guid())
    graph = GraphStore(top_level=parent_node, nodes={0: parent_node})
    graph.add_node(GraphNode(fea_res.name, graph.next_node_id(), hash=create_guid(), parent=parent_node))
    scene.metadata.update(graph.create_meta())
    return scene


def scene_from_fem(
    fem: FEM, params: RenderParams, graph: GraphStore = None, scene: trimesh.Scene = None
) -> trimesh.Scene:
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    shell_color = Color.from_str("white")
    shell_color_id = 100000
    line_color = Color.from_str("gray")
    line_color_id = 100001
    points_color = Color.from_str("black")
    points_color_id = 100002
    solid_bm_color = Color.from_str("light-gray")
    solid_bm_color_id = 100003

    if graph is None:
        if fem.parent is not None:
            graph = fem.parent.get_graph_store()
            parent_node = graph.hash_map.get(fem.parent.guid)
        else:
            parent_node = GraphNode("world", 0, hash=create_guid())
            graph = GraphStore(top_level=parent_node, nodes={0: parent_node})
    else:
        parent_node = graph.top_level

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

    base_frame = graph.top_level.name if graph is not None else "root"
    scene = trimesh.Scene(base_frame=base_frame) if scene is None else scene
    line_elems = list(fem.elements.lines)

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

        merged_mesh_to_trimesh_scene(scene, merged_store, solid_bm_color, solid_bm_color_id, graph_store=graph)

    if len(edge_store.indices) > 0:
        merged_mesh_to_trimesh_scene(scene, edge_store, line_color, line_color_id, graph_store=graph)

    if len(face_store.indices) > 0:
        merged_mesh_to_trimesh_scene(scene, face_store, shell_color, shell_color_id, graph_store=graph)

    if len(points_store.position) > 0:
        merged_mesh_to_trimesh_scene(scene, points_store, points_color, points_color_id, graph_store=graph)

    scene.metadata.update(graph.create_meta())

    return scene


def scene_from_object(physical_object: BackendGeom, params: RenderParams) -> trimesh.Scene:
    from itertools import groupby

    from ada import Pipe
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.optimize import concatenate_stores
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    bt = BatchTessellator()

    root = GraphNode("world", 0, hash=create_guid())
    graph_store = GraphStore(top_level=root, nodes={0: root})
    node = graph_store.add_node(
        GraphNode(physical_object.name, graph_store.next_node_id(), hash=physical_object.guid, parent=root)
    )

    if isinstance(physical_object, Pipe):
        physical_objects = physical_object.segments
        for seg in physical_objects:
            graph_store.add_node(GraphNode(seg.name, graph_store.next_node_id(), hash=seg.guid, parent=node))
    else:
        physical_objects = [physical_object]

    mesh_stores = list(bt.batch_tessellate(physical_objects))
    scene = trimesh.Scene()
    mesh_map = []
    for mat_id, meshes in groupby(mesh_stores, lambda x: x.material):
        meshes = list(meshes)

        merged_store = concatenate_stores(meshes)
        mesh_map.append((mat_id, meshes, merged_store))

        merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, graph_store)

    scene.metadata.update(graph_store.create_meta())
    return scene


def scene_from_part_or_assembly(
    part_or_assembly: Part | Assembly, apply_transform, params: RenderParams
) -> trimesh.Scene:
    from ada import Assembly

    if params.auto_sync_ifc_store and isinstance(part_or_assembly, Assembly):
        part_or_assembly.ifc_store.sync()

    scene = part_or_assembly.to_trimesh_scene(
        stream_from_ifc=params.stream_from_ifc_store,
        merge_meshes=params.merge_meshes,
        params=params,
        apply_transform=apply_transform,
    )
    return scene


class RendererManager:
    def __init__(
        self,
        renderer: Literal["react", "pygfx", "trimesh"],
        host: str = "localhost",
        ws_port: int = 8765,
        server_exe: pathlib.Path = None,
        server_args: list[str] = None,
        run_ws_in_thread: bool = False,
        ping_timeout=1,
        embed_glb: bool = False,
    ):
        self.renderer = renderer
        self.host = host
        self.ws_port = ws_port
        self.server_exe = server_exe
        self.server_args = server_args
        self.run_ws_in_thread = run_ws_in_thread
        self._is_in_notebook = None
        self.ping_timeout = ping_timeout
        self.embed_glb = embed_glb

    def start_server(self):
        """Set up the WebSocket server and renderer."""
        self._start_websocket_server()

    def _start_websocket_server(self):
        """Starts the WebSocket server if needed."""
        from ada.comms.wsockets_utils import start_ws_async_server

        if self.renderer == "pygfx":
            from ada.visit.rendering.render_pygfx import PYGFX_RENDERER_EXE_PY

            self.server_exe = PYGFX_RENDERER_EXE_PY

        start_ws_async_server(
            server_exe=self.server_exe,
            server_args=self.server_args,
            host=self.host,
            port=self.ws_port,
            run_in_thread=self.run_ws_in_thread,
        )

    def is_in_notebook(self):
        if self._is_in_notebook is None:
            from ada.visit.utils import in_notebook

            self._is_in_notebook = in_notebook()

        return self._is_in_notebook

    def ensure_liveness(self, wc, target_id=None) -> None | HTML:
        """Ensures that the WebSocket client is connected and target is live."""
        if not self.is_in_notebook():
            target_id = None  # Currently does not support unique viewer IDs outside of notebooks

        if wc.check_target_liveness(target_id=target_id, timeout=self.ping_timeout):
            # The target is alive meaning a viewer is running
            return None

        renderer = None
        if self.renderer == "react":
            from ada.visit.rendering.renderer_react import RendererReact

            renderer_obj = RendererReact()
            if self.is_in_notebook():
                renderer = renderer_obj.get_notebook_renderer_widget(target_id=target_id)
            else:
                renderer = renderer_obj.show()

        return renderer

    @staticmethod
    def obj_to_trimesh(
        obj: BackendGeom | Part | Assembly | FEAResult | FEM | trimesh.Scene | MeshDC,
        params: RenderParams,
        apply_transform=True,
    ) -> trimesh.Scene:
        from ada import FEM, Assembly, Part
        from ada.base.physical_objects import BackendGeom
        from ada.fem.results.common import FEAResult

        if type(obj) is Part or type(obj) is Assembly:
            scene = scene_from_part_or_assembly(obj, apply_transform, params)
        elif isinstance(obj, BackendGeom):
            scene = scene_from_object(obj, params)
        elif isinstance(obj, FEM):
            scene = scene_from_fem(obj, params)
        elif isinstance(obj, FEAResult):
            scene = scene_from_fem_results(obj, params)
        elif isinstance(obj, trimesh.Scene):
            scene = obj
        else:
            raise ValueError(f"Unsupported object type: {type(obj)}")

        return scene

    def render(
        self,
        obj: BackendGeom | Part | Assembly | FEAResult | FEM | trimesh.Scene | MeshDC,
        params: RenderParams,
        apply_transform=True,
        force_ws=False,
        auto_embed_glb_in_notebook=True,
    ) -> HTML | None:
        from ada import Assembly
        from ada.comms.wsock_client_sync import WebSocketClientSync

        if self.renderer == "trimesh":
            scene = RendererManager.obj_to_trimesh(obj, params, apply_transform)
            return scene.show()

        if self.is_in_notebook() and auto_embed_glb_in_notebook:
            self.embed_glb = True

        if self.embed_glb:
            from ada.visit.rendering.renderer_react import RendererReact

            renderer_obj = RendererReact()
            scene = RendererManager.obj_to_trimesh(obj, params, apply_transform)
            if self.is_in_notebook():
                renderer = renderer_obj.get_notebook_renderer_widget(
                    target_id=None, embed_trimesh_scene=scene, force_ws=force_ws
                )
                return renderer
            else:
                return renderer_obj.serve_html(embed_trimesh_scene=scene, force_ws=force_ws)

        # Set up the renderer and WebSocket server
        self.start_server()
        # target_id = params.unique_id
        if self.is_in_notebook():
            target_id = params.unique_id
        else:
            target_id = None  # Currently does not support unique viewer IDs outside of notebooks

        with WebSocketClientSync(self.host, self.ws_port) as wc:
            renderer_instance = self.ensure_liveness(wc, target_id=target_id)

            if isinstance(obj, MeshDC):
                wc.append_scene(obj)
                return renderer_instance
            else:
                scene = RendererManager.obj_to_trimesh(obj, params, apply_transform)

            if params.scene.operation == SceneOperationsDC.ADD:
                mesh: trimesh.Trimesh = scene.to_mesh()
                verts = mesh.vertices.flatten().tolist()
                faces = mesh.faces.flatten().tolist()
                parent_name = None
                if hasattr(obj, "parent") and obj.parent is not None:
                    parent_name = obj.parent.name
                name = obj.name if hasattr(obj, "name") else "Scene"
                mesh_dc = MeshDC(name, faces, verts, parent_name)
                wc.append_scene(mesh_dc)
                return renderer_instance

            if params.scene_post_processor is not None:
                scene = params.scene_post_processor(scene)

            # Send the scene to the WebSocket client
            wc.update_scene(
                obj.name if hasattr(obj, "name") else "Scene",
                scene,
                purpose=params.purpose,
                scene_op=params.scene.operation,
                gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
                gltf_tree_postprocessor=params.gltf_tree_postprocessor,
                target_id=target_id,
            )

            if params.gltf_export_to_file is not None:
                gltf_tree_postprocess = GltfTreePostProcessor(params.gltf_asset_extras_dict)
                gltf_export_to_file = params.gltf_export_to_file
                if isinstance(gltf_export_to_file, str):
                    gltf_export_to_file = pathlib.Path(params.gltf_export_to_file)
                gltf_export_to_file.parent.mkdir(parents=True, exist_ok=True)
                scene.export(gltf_export_to_file, tree_postprocessor=gltf_tree_postprocess)

            if params.add_ifc_backend is True and type(obj) is Assembly:
                server_temp = Config().websockets_server_temp_dir
                if server_temp is not None:
                    backend_file_dir = server_temp
                elif params.backend_file_dir is not None:
                    backend_file_dir = params.backend_file_dir
                else:
                    backend_file_dir = pathlib.Path.cwd() / "temp"

                if isinstance(backend_file_dir, str):
                    backend_file_dir = pathlib.Path(backend_file_dir)

                ifc_file = backend_file_dir / f"{obj.name}.ifc"
                obj.to_ifc(ifc_file)

                wc.update_file_server(
                    FileObjectDC(
                        name=obj.name, file_type=FileTypeDC.IFC, purpose=FilePurposeDC.DESIGN, filepath=ifc_file
                    )
                )

        return renderer_instance


class GltfTreePostProcessor:
    def __init__(self, gltf_asset_extras_dict: dict):
        self.gltf_asset_extras_dict = gltf_asset_extras_dict

    def __call__(self, tree):
        if self.gltf_asset_extras_dict is not None:
            extras = tree.get("asset", {}).get("extras", {})
            extras.update(self.gltf_asset_extras_dict)
            tree["asset"]["extras"] = extras
