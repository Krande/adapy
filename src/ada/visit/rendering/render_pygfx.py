import pathlib
from multiprocessing import Process, Queue
from typing import Callable, Iterable

import numpy as np
import pygfx as gfx
import trimesh
import trimesh.visual.material
from rendercanvas.auto import RenderCanvas, loop

import ada.visit.rendering.pygfx_helpers as gfx_utils
from ada import Part
from ada.base.types import GeomRepr
from ada.comms.wsock_client_sync import WebSocketClientSync
from ada.config import logger
from ada.core.guid import create_guid
from ada.core.vector_utils import unit_vector
from ada.geom import Geometry
from ada.occ.tessellating import BatchTessellator
from ada.visit.colors import Color
from ada.visit.render_params import RenderParams
from ada.visit.rendering.render_backend import (
    MeshInfo,
    SqLiteBackend,
    create_selected_meshes_from_mesh_info,
)
from ada.visit.scene_converter import SceneConverter

# from rendercanvas.pyside6 import RenderCanvas, loop


PYGFX_RENDERER_EXE_PY = pathlib.Path(__file__)


BG_GRAY = Color(57, 57, 57)
PICKED_COLOR = Color(0, 123, 255)


class RendererPyGFX:
    def __init__(self, render_backend=SqLiteBackend(), canvas_title: str = "PyGFX Renderer", no_gui: bool = False):
        self.backend = render_backend
        self._mesh_map = {}
        self._selected_mat = gfx.MeshPhongMaterial(color=PICKED_COLOR, flat_shading=True)
        self.selected_mesh = None
        self._selected_mesh_info: MeshInfo = None
        self._original_geometry = None
        self._original_mesh = None
        self.scene = gfx.Scene()
        self.scene.add(gfx.Background(None, gfx.BackgroundMaterial(BG_GRAY.hex)))
        self._scene_objects = gfx.Group()
        self._scene_objects.receive_shadow = True
        self._scene_objects.cast_shadow = True
        self.scene.add(self._scene_objects)
        if no_gui:
            self._canvas = None
            self._renderer = None
        else:
            self._canvas = RenderCanvas(title=canvas_title, max_fps=60)
            self._renderer = gfx.renderers.WgpuRenderer(self._canvas)  # , show_fps=False)

        self.before_render = None
        self.after_render = None
        self._controller = None
        self.on_click_pre: Callable[[gfx.PointerEvent], None] | None = None
        self.on_click_post: Callable[[gfx.PointerEvent, MeshInfo], None] | None = None
        self._init_scene()
        self.process_terminate_on_end: Process | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.process_terminate_on_end is not None:
            logger.info("Terminating websockets server")
            self.process_terminate_on_end.terminate()

    def _init_scene(self):
        scene = self.scene
        dir_light = gfx.DirectionalLight()
        camera = gfx.PerspectiveCamera(70, 1, depth_range=(0.1, 1000))
        self._camera = camera

        scene.add(camera)
        scene.add(dir_light)
        camera.add(dir_light)
        scene.add(gfx.AmbientLight())
        scene.add(gfx_utils.AxesHelper())

    def _get_scene_meshes(self, scene: trimesh.Scene, tag: str) -> Iterable[gfx.Mesh]:
        for key, m in scene.geometry.items():
            mesh = gfx_utils.gfx_mesh_from_mesh(m)
            if "node" in key:
                buffer_id = int(float(key.replace("node", "")))
            else:
                buffer_id = len(self._mesh_map)
            self._mesh_map[mesh.id] = (tag, buffer_id)
            yield mesh

    def add_geom(self, geom: Geometry, name: str, guid: str, tag=create_guid(), metadata=None):
        bt = BatchTessellator()

        geom_mesh = bt.tessellate_geom(geom)
        mat = gfx.MeshPhongMaterial(color=geom.color.rgb, flat_shading=True)
        mesh = gfx.Mesh(gfx_utils.geometry_from_mesh(geom_mesh), material=mat)

        metadata = metadata if metadata else {}
        metadata["meta"] = {guid: (name, "*")}
        metadata["idsequence0"] = {guid: (0, len(geom_mesh.position))}
        self._mesh_map[mesh.id] = (tag, 0)
        self._scene_objects.add(mesh)
        self.backend.add_metadata(metadata, tag)

    def add_part(self, part: Part, render_override: dict[str, GeomRepr] = None, params: RenderParams = None):
        if params is None:
            params = RenderParams(render_override=render_override)

        converter = SceneConverter(part, params)
        scene = converter.build_processed_scene()

        self.add_trimesh_scene(scene, part.name)

    def add_trimesh_scene(self, trimesh_scene: trimesh.Scene, tag: str):
        from ada.visit.scene_handling.scene_utils import from_z_to_y_is_up

        rotated_scene = trimesh_scene.copy()
        from_z_to_y_is_up(rotated_scene, transform_all_geom=True)

        meshes = self._get_scene_meshes(rotated_scene, tag)
        self._scene_objects.add(*meshes)
        self.backend.add_metadata(rotated_scene.metadata, tag)

    def load_glb_files_into_scene(self, glb_files: Iterable[pathlib.Path]):
        num_scenes = 0
        start_meshes = len(self._scene_objects.children)

        for glb_file in glb_files:
            num_scenes += 1
            scene = self.backend.glb_to_trimesh_scene(glb_file)
            self.add_trimesh_scene(scene, glb_file.stem, False)
            self.backend.commit()

        num_meshes = len(self._scene_objects.children) - start_meshes
        print(f"Loaded {num_meshes} meshes from {num_scenes} glb files")
        self.backend.commit()

    def on_click(self, event: gfx.PointerEvent):

        if self.on_click_pre is not None:
            self.on_click_pre(event)

        info = event.pick_info
        print(f"Clicked: {info=}")
        if event.button != 1:
            return

        obj = info.get("world_object", None)
        if isinstance(obj, gfx.Mesh):
            mesh: gfx.Mesh = event.target
            geom_index = info.get("face_index", None) * 3  # Backend uses a flat array of indices
        elif isinstance(obj, gfx.Line):
            geom_index = info.get("vertex_index", None) * 2  # Backend uses a flat array of indices
            mesh: gfx.Line = event.target
        elif isinstance(obj, gfx.Points):
            geom_index = info.get("vertex_index", None)
            mesh: gfx.Points = event.target
        else:
            logger.debug("No mesh selected")
            return

        # Get what face was clicked
        res = self._mesh_map.get(mesh.id, None)
        if res is None:
            print("Could not find mesh id in map")
            return

        glb_fname, buffer_id = res

        if self.selected_mesh is not None:
            if (
                isinstance(obj, gfx.Mesh)
                and buffer_id == self._selected_mesh_info.buffer_id
                and geom_index > self._selected_mesh_info.start
            ):
                face_index_bump = 1 + self._selected_mesh_info.end - self._selected_mesh_info.start
                logger.info(f"Adding {face_index_bump} to {geom_index=}")
                geom_index += face_index_bump

            self.scene.remove(self.selected_mesh)

        mesh_data = self.backend.get_mesh_data_from_face_index(geom_index, buffer_id, glb_fname)

        if mesh_data is None:
            logger.error(f"Could not find data for {mesh} with {geom_index=}, {buffer_id=} and {glb_fname=}")
            return

        self._selected_mesh_info = mesh_data
        if self.selected_mesh is not None:
            self.scene.remove(self.selected_mesh)
            del self.selected_mesh

        if self._original_geometry is not None:
            self._original_mesh.geometry = self._original_geometry

        if isinstance(mesh, gfx.Mesh):
            self._original_geometry = gfx.Geometry(
                positions=mesh.geometry.positions.data,
                indices=mesh.geometry.indices.data,
            )
            self._original_mesh = mesh
            self.selected_mesh = highlight_clicked_mesh(mesh, mesh_data, self._selected_mat)
        elif isinstance(mesh, gfx.Line):
            self.selected_mesh = highlight_clicked_line(mesh, self._selected_mat.color)
        elif isinstance(mesh, gfx.Points):
            self.selected_mesh = highlight_clicked_points(mesh, mesh_data, self._selected_mat.color)
        else:
            raise NotImplementedError()

        self.scene.add(self.selected_mesh)

        if self.on_click_post is not None:
            self.on_click_post(event, mesh_data)
        else:
            print(mesh_data, event.pick_info)

    def _add_event_handlers(self):
        ob = self._scene_objects
        ob.add_event_handler(self.on_click, "pointer_down", "pointer_up")
        # ob.add_event_handler(
        #     self.on_click, "pointer_down", "pointer_up", "pointer_move", "pointer_out", "pointer_over"
        # )

    def animate(self):
        self._renderer.render(self.scene, self._camera)
        self._canvas.request_draw()

    def show(self):
        bbox = self.scene.get_world_bounding_box()
        grid_scale = 1.5 * max(bbox[1] - bbox[0])
        grid = gfx.GridHelper(grid_scale, 10)
        self.scene.add(grid)
        self._add_event_handlers()
        x, y, z, r = self.scene.get_world_bounding_sphere()
        view_pos = np.array([x, y, z]) - r * 5
        view_dir = unit_vector(view_pos + np.array([x, y, z]))
        self._camera.show_object(self.scene, view_dir=view_dir)
        self._controller = gfx.OrbitController(camera=self._camera, register_events=self._renderer)
        self._canvas.request_draw(lambda: self._renderer.render(self.scene, self._camera))

        loop.run()


def highlight_clicked_mesh(mesh: gfx.Mesh, mesh_data: MeshInfo, material: gfx.MeshPhongMaterial) -> gfx.Mesh:
    geom = mesh.geometry
    sel_meshes = create_selected_meshes_from_mesh_info(mesh_data, geom.indices.data, geom.positions.data)

    selected_mesh = gfx_utils.gfx_mesh_from_mesh(sel_meshes.selected_mesh, material)

    modified_mesh = gfx_utils.gfx_mesh_from_mesh(sel_meshes.modified_mesh, mesh.material)
    mesh.geometry = modified_mesh.geometry

    return selected_mesh


def highlight_clicked_line(mesh: gfx.Line, color: gfx.Color) -> gfx.Line:
    c_mesh = gfx.Line(mesh.geometry, gfx.LineSegmentMaterial(thickness=3, color=color))
    return c_mesh


def highlight_clicked_points(mesh: gfx.Points, mesh_data: MeshInfo, color: gfx.Color) -> gfx.Points:
    s = mesh_data.start
    e = mesh_data.end + 1
    selected_positions = mesh.geometry.positions.data[s:e]

    c_mesh = gfx.Points(
        gfx.Geometry(positions=selected_positions),
        gfx.PointsMaterial(size=15, color=color),
    )
    return c_mesh


def scale_tri_mesh(mesh: trimesh.Trimesh, sfac: float):
    # Calculate volumetric center
    center = mesh.center_mass

    # Create translation matrices
    translate_to_origin = trimesh.transformations.translation_matrix(-center)
    translate_back = trimesh.transformations.translation_matrix(center)

    # Create scale matrix
    scale_matrix = trimesh.transformations.scale_matrix(sfac, center)

    # Combine transformations
    transform = translate_back @ scale_matrix @ translate_to_origin

    # Apply the transformation
    mesh.apply_transform(transform)


def start_server(shared_queue: Queue = None, host="localhost", port=8765) -> None:
    ws = WebSocketClientSync(host=host, port=port)
    ws.connect()
    while True:
        msg = ws.receive(1)
        if msg:
            shared_queue.put()


def start_pygfx_viewer(host="localhost", port="8765", scene=None):
    with RendererPyGFX(render_backend=SqLiteBackend()) as render:
        if scene is not None:
            render.add_trimesh_scene(scene, tag="userdata")

        render.show()


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    start_pygfx_viewer(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
