from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Optional, OrderedDict

import trimesh

from ada.comms.fb_model_gen import FilePurposeDC

if TYPE_CHECKING:
    from IPython.display import HTML

    from ada import Assembly, Part
    from ada.base.physical_objects import BackendGeom


@dataclass
class RenderAssemblyParams:
    auto_sync_ifc_store: bool = False
    stream_from_ifc_store: bool = False
    merge_meshes: bool = True
    scene_post_processor: Optional[Callable[[trimesh.Scene], trimesh.Scene]] = None
    purpose: Optional[FilePurposeDC] = FilePurposeDC.DESIGN
    gltf_buffer_postprocessor: Optional[Callable[[OrderedDict, dict], None]] = None
    add_ifc_backend: bool = False
    backend_file_dir: Optional[str] = None
    unique_id: int = None

    def __post_init__(self):
        # ensure that if unique_id is set, it is a 32-bit integer
        if self.unique_id is not None:
            self.unique_id = self.unique_id & 0xFFFFFFFF


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


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, params: RenderAssemblyParams) -> trimesh.Scene:
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
    ):
        self.renderer = renderer
        self.host = host
        self.port = port
        self.server_exe = server_exe
        self.server_args = server_args
        self.run_ws_in_thread = run_ws_in_thread
        self._is_in_notebook = None

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

        if wc.check_target_liveness(target_id=target_id):
            # The target is alive meaning a viewer is running
            return None

        renderer = None
        if self.renderer == "react":
            from ada.visit.rendering.renderer_react import RendererReact

            renderer = RendererReact().show()

        return renderer

    def render(self, obj: BackendGeom | Part | Assembly, params: RenderAssemblyParams) -> HTML | None:
        from ada import Assembly, Part
        from ada.base.physical_objects import BackendGeom
        from ada.comms.wsock_client_sync import WebSocketClientSync

        # Set up the renderer and WebSocket server
        self.start_server()

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
            else:
                raise ValueError(f"Unsupported object type: {type(obj)}")

            if params.scene_post_processor is not None:
                scene = params.scene_post_processor(scene)

            # Send the scene to the WebSocket client
            wc.update_scene(
                obj.name,
                scene,
                purpose=params.purpose,
                gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
                target_id=target_id,
            )

        return renderer_instance
