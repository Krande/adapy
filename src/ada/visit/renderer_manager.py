from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Literal

from ada.comms.fb_wrap_model_gen import FileObjectDC, FilePurposeDC, FileTypeDC, MeshDC
from ada.config import Config
from ada.visit.render_params import RenderParams
from ada.visit.scene_converter import SceneConverter

if TYPE_CHECKING:
    import trimesh
    from IPython.display import HTML

    from ada import FEM, Assembly, Part
    from ada.base.physical_objects import BackendGeom
    from ada.fem.results.common import FEAResult


class RendererManager:
    def __init__(
        self,
        renderer: Literal["react", "pygfx", "trimesh"] = "react",
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

    def render(
        self,
        obj: BackendGeom | Part | Assembly | FEAResult | FEM | trimesh.Scene | MeshDC,
        params: RenderParams,
        force_ws=False,
        auto_embed_glb_in_notebook=True,
        force_embed_glb=False,
        always_use_external_viewer=False,
    ) -> HTML | None:
        """
        Render the given object using the specified renderer.


        Parameters:
        - obj: The object to render, can be a BackendGeom, Part, Assembly, FEAResult, FEM, trimesh.Scene, or MeshDC.
        - params: RenderParams object containing rendering parameters.
        - force_ws: If True, forces the use of WebSocket for rendering.
        - auto_embed_glb_in_notebook: If True, automatically embeds GLB in Jupyter Notebook.
        - force_embed_glb: If True, forces embedding of GLB in the viewer.
        - always_use_external_viewer: If True, always uses an external viewer even if in a notebook.
        """
        import trimesh

        from ada import Assembly
        from ada.comms.wsock_client_sync import WebSocketClientSync
        from ada.visit.rendering.renderer_react import RendererReact

        converter = SceneConverter(obj, params)

        if self.renderer == "trimesh":
            scene = converter.build_processed_scene()
            return scene.show()

        if self.renderer == "pygfx":
            from ada.visit.rendering.render_pygfx import start_pygfx_viewer

            scene = converter.build_processed_scene()

            return start_pygfx_viewer(port=params.serve_ws_port, scene=scene)

        if (
            self.is_in_notebook() and auto_embed_glb_in_notebook and always_use_external_viewer is False
        ) or force_embed_glb:
            self.embed_glb = True

        renderer_obj = RendererReact()
        if self.embed_glb:
            encoded = converter.build_encoded_glb()
            if self.is_in_notebook() and always_use_external_viewer is False:
                renderer = renderer_obj.get_notebook_renderer_widget(
                    target_id=None, embed_base64_glb=encoded, force_ws=force_ws
                )
                return renderer
            else:
                return renderer_obj.serve_html(
                    web_port=params.serve_web_port,
                    ws_port=params.serve_ws_port,
                    embed_base64_glb=encoded,
                    force_ws=force_ws,
                    gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
                    gltf_tree_postprocessor=params.gltf_tree_postprocessor,
                )

        # Set up the renderer and WebSocket server
        self.start_server()
        if params.serve_html:
            encoded = converter.build_encoded_glb()
            return renderer_obj.serve_html(
                web_port=params.serve_web_port,
                ws_port=params.serve_ws_port,
                embed_base64_glb=encoded,
                force_ws=force_ws,
                gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
                gltf_tree_postprocessor=params.gltf_tree_postprocessor,
            )

        if self.is_in_notebook() and always_use_external_viewer is False:
            target_id = params.unique_id
        else:
            target_id = None  # Currently does not support unique viewer IDs outside of notebooks

        with WebSocketClientSync(self.host, self.ws_port) as wc:
            renderer_instance = self.ensure_liveness(wc, target_id=target_id)

            if isinstance(obj, MeshDC):
                wc.append_scene(obj)
                return renderer_instance
            else:
                scene = converter.build_processed_scene()

            if isinstance(obj, trimesh.Scene):
                scene_name = obj.source.file_name.split(".")[0] if obj.source.file_name is not None else "Scene"
            else:
                scene_name = obj.name if hasattr(obj, "name") else "Scene"

            # Send the scene to the WebSocket client
            wc.update_scene(
                scene_name,
                scene,
                purpose=params.purpose,
                scene_op=params.scene.operation,
                gltf_buffer_postprocessor=converter.buffer_postprocessor,
                gltf_tree_postprocessor=converter.tree_postprocessor,
                target_id=target_id,
            )

            if params.gltf_export_to_file is not None:
                if params._gltf_tree_postprocessor is None:
                    gltf_tree_postprocess = GltfTreePostProcessor(params.gltf_asset_extras_dict)
                else:
                    gltf_tree_postprocess = params._gltf_tree_postprocessor
                gltf_export_to_file = params.gltf_export_to_file
                if isinstance(gltf_export_to_file, str):
                    gltf_export_to_file = pathlib.Path(params.gltf_export_to_file)
                gltf_export_to_file.parent.mkdir(parents=True, exist_ok=True)
                scene.export(
                    gltf_export_to_file,
                    tree_postprocessor=gltf_tree_postprocess,
                    buffer_postprocessor=params.gltf_buffer_postprocessor,
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

    def send_glb_file_to_viewer(self, name, glb_file: str | pathlib.Path, target_id=None, params: RenderParams = None):
        import gzip

        from ada.comms.wsock_client_sync import WebSocketClientSync
        from ada.visit.rendering.render_backend import is_gzip_file

        if is_gzip_file(glb_file):
            with gzip.open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")
        else:
            with open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")

        if params is None:
            params = RenderParams(serve_html=True)

        with WebSocketClientSync(self.host, self.ws_port) as wc:
            self.ensure_liveness(wc, target_id=target_id)
            # Send the scene to the WebSocket client
            wc.update_scene(
                name,
                scene,
                purpose=params.purpose,
                scene_op=params.scene.operation,
                gltf_buffer_postprocessor=params.gltf_buffer_postprocessor,
                gltf_tree_postprocessor=params.gltf_tree_postprocessor,
                target_id=target_id,
            )
        return scene


class GltfTreePostProcessor:
    def __init__(self, gltf_asset_extras_dict: dict):
        self.gltf_asset_extras_dict = gltf_asset_extras_dict

    def __call__(self, tree):
        if self.gltf_asset_extras_dict is not None:
            extras = tree.get("asset", {}).get("extras", {})
            extras.update(self.gltf_asset_extras_dict)
            tree["asset"]["extras"] = extras
