from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, Optional, OrderedDict

import numpy as np
import trimesh

from ada.comms.fb_model_gen import (
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    SceneDC,
    SceneOperationsDC,
)
from ada.config import Config

if TYPE_CHECKING:
    from IPython.display import HTML

    from ada import Assembly, Part
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


@dataclass
class RenderParams:
    auto_sync_ifc_store: bool = False
    stream_from_ifc_store: bool = False
    merge_meshes: bool = True
    scene_post_processor: Optional[Callable[[trimesh.Scene], trimesh.Scene]] = None
    purpose: Optional[FilePurposeDC] = FilePurposeDC.DESIGN
    scene: SceneDC = None
    gltf_buffer_postprocessor: Optional[Callable[[OrderedDict, dict], None]] = None
    gltf_tree_postprocessor: Optional[Callable[[OrderedDict], None]] = None
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


def scene_from_fem_results(self: FEAResult, params: RenderParams):
    from trimesh.path.entities import Line

    from ada.api.animations import Animation, AnimationStore
    from ada.core.vector_transforms import rot_matrix

    warp_scale = params.fea_params.warp_scale

    # initial mesh
    vertices = self.mesh.nodes.coords
    edges, faces = self.mesh.get_edges_and_faces_from_mesh()

    faces_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)

    entities = [Line(x) for x in edges]
    edge_mesh = trimesh.path.Path3D(entities=entities, vertices=vertices)

    scene = trimesh.Scene()
    face_node = scene.add_geometry(faces_mesh, node_name=self.name, geom_name="faces")
    _ = scene.add_geometry(edge_mesh, node_name=f"{self.name}_edges", geom_name="edges", parent_node_name=self.name)

    face_node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == face_node][0]
    # edge_node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == edge_node][0]

    # React renderer supports animations
    animation_store = AnimationStore()

    # Loop over the results and create an animation from it
    vertices = self.mesh.nodes.coords
    added_results = []
    for i, result in enumerate(self.results):
        warped_vertices = self._warp_data(vertices, result.name, result.step, warp_scale)
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

    return scene


def scene_from_object(physical_object: BackendGeom) -> trimesh.Scene:
    from itertools import groupby

    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.optimize import concatenate_stores
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    bt = BatchTessellator()
    mesh_stores = list(bt.batch_tessellate([physical_object]))
    scene = trimesh.Scene()
    mesh_map = []

    for mat_id, meshes in groupby(mesh_stores, lambda x: x.material):
        meshes = list(meshes)

        merged_store = concatenate_stores(meshes)
        mesh_map.append((mat_id, meshes, merged_store))

        merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, None)

    return scene


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, params: RenderParams) -> trimesh.Scene:
    from ada import Assembly

    if params.auto_sync_ifc_store and isinstance(part_or_assembly, Assembly):
        part_or_assembly.ifc_store.sync()

    scene = part_or_assembly.to_trimesh_scene(
        stream_from_ifc=params.stream_from_ifc_store, merge_meshes=params.merge_meshes
    )
    return scene


class RendererManager:
    def __init__(
        self,
        renderer: Literal["react", "pygfx"],
        host: str = "localhost",
        port: int = 8765,
        server_exe: pathlib.Path = None,
        server_args: list[str] = None,
        run_ws_in_thread: bool = False,
        ping_timeout=1,
    ):
        self.renderer = renderer
        self.host = host
        self.port = port
        self.server_exe = server_exe
        self.server_args = server_args
        self.run_ws_in_thread = run_ws_in_thread
        self._is_in_notebook = None
        self.ping_timeout = ping_timeout

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
            port=self.port,
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

    def render(self, obj: BackendGeom | Part | Assembly | FEAResult, params: RenderParams) -> HTML | None:
        from ada import Assembly, Part
        from ada.base.physical_objects import BackendGeom
        from ada.comms.wsock_client_sync import WebSocketClientSync
        from ada.fem.results.common import FEAResult

        # Set up the renderer and WebSocket server
        self.start_server()
        # target_id = params.unique_id
        if self.is_in_notebook():
            target_id = params.unique_id
        else:
            target_id = None  # Currently does not support unique viewer IDs outside of notebooks

        with WebSocketClientSync(self.host, self.port) as wc:
            renderer_instance = self.ensure_liveness(wc, target_id=target_id)

            if type(obj) is Part or type(obj) is Assembly:
                scene = scene_from_part_or_assembly(obj, params)
            elif isinstance(obj, BackendGeom):
                scene = scene_from_object(obj)
            elif isinstance(obj, FEAResult):
                scene = scene_from_fem_results(obj, params)
            else:
                raise ValueError(f"Unsupported object type: {type(obj)}")

            if params.scene_post_processor is not None:
                scene = params.scene_post_processor(scene)

            # Send the scene to the WebSocket client
            wc.update_scene(
                obj.name,
                scene,
                purpose=params.purpose,
                scene_op=params.scene.operation,
                gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
                gltf_tree_postprocessor=params.gltf_tree_postprocessor,
                target_id=target_id,
            )

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
