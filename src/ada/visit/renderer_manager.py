from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Optional

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
    scene_post_processor: Optional[Callable] = None
    purpose: Optional[FilePurposeDC] = FilePurposeDC.DESIGN
    gltf_buffer_postprocessor: Optional[Callable] = None
    add_ifc_backend: bool = False
    backend_file_dir: Optional[str] = None


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

    def setup_renderer(self):
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

    def _get_renderer_instance(self):
        """Returns the renderer instance if running in a notebook."""
        if self.is_in_notebook() and self.renderer == "react":
            from ada.visit.rendering.renderer_react import RendererReact

            return RendererReact().show()

        return None

    def is_in_notebook(self):
        if self._is_in_notebook is None:
            from ada.visit.utils import in_notebook

            self._is_in_notebook = in_notebook()

        return self._is_in_notebook

    def ensure_liveness(self, wc) -> None | HTML:
        """Ensures that the WebSocket client is connected and target is live."""

        if wc.check_target_liveness():
            # The target is alive meaning a viewer is running
            return None

        renderer = None
        if self.renderer == "react":
            from ada.visit.rendering.renderer_react import RendererReact

            renderer = RendererReact().show()

            # while not wc.check_target_liveness():
            #     pass

        return renderer

    def render_physical_object(self, physical_object: BackendGeom, params: RenderAssemblyParams) -> HTML | None:
        # from ada.comms.wsock_client_async import WebSocketClientAsync
        from itertools import groupby

        from ada.comms.wsock_client_sync import WebSocketClientSync
        from ada.occ.tessellating import BatchTessellator
        from ada.visit.gltf.optimize import concatenate_stores
        from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

        # Set up the renderer and WebSocket server
        self.setup_renderer()

        bt = BatchTessellator()
        mesh_stores = list(bt.batch_tessellate([physical_object]))
        scene = trimesh.Scene()
        mesh_map = []

        for mat_id, meshes in groupby(mesh_stores, lambda x: x.material):
            meshes = list(meshes)

            merged_store = concatenate_stores(meshes)
            mesh_map.append((mat_id, meshes, merged_store))

            merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, None)

        with WebSocketClientSync(self.host, self.port) as wc:
            renderer_instance = self.ensure_liveness(wc)

            if params.scene_post_processor is not None:
                scene = params.scene_post_processor(scene)

            # Send the scene to the WebSocket client
            wc.update_scene(
                physical_object.name,
                scene,
                purpose=params.purpose,
                gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
            )

        return renderer_instance

    def render_part_or_assembly(self, assembly: Assembly | Part, params: RenderAssemblyParams) -> HTML | None:
        from ada import Assembly

        # from ada.comms.wsock_client_async import WebSocketClientAsync
        from ada.comms.wsock_client_sync import WebSocketClientSync

        # Set up the renderer and WebSocket server
        self.setup_renderer()

        with WebSocketClientSync(self.host, self.port) as wc:
            renderer_instance = self.ensure_liveness(wc)

            if params.auto_sync_ifc_store and isinstance(assembly, Assembly):
                assembly.ifc_store.sync()

            scene: trimesh.Scene = assembly.to_trimesh_scene(
                stream_from_ifc=params.stream_from_ifc_store, merge_meshes=params.merge_meshes
            )

            if params.scene_post_processor is not None:
                scene = params.scene_post_processor(scene)

            # Send the scene to the WebSocket client
            wc.update_scene(
                assembly.name, scene, purpose=params.purpose, gltf_buffer_postprocessor=params.gltf_buffer_postprocessor
            )

        return renderer_instance
