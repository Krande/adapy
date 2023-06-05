# pip install -U pygfx glfw

import pathlib
from itertools import groupby
from typing import Iterable, Callable

import numpy as np
import trimesh
import trimesh.visual.material

from ada import Part
import glfw
from ada.base.types import GeomRepr
from ada.cadit.ifc.utils import create_guid
from ada.geom import Geometry
from ada.occ.tessellating import BatchTessellator
from ada.visit.colors import Color
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

try:
    import pygfx as gfx

    import ada.visit.render_pygfx_helpers as gfx_utils
except ImportError:
    raise ImportError("Please install pygfx to use this renderer -> 'pip install pygfx'.")
try:
    from wgpu.gui.auto import WgpuCanvas
except ImportError:
    raise ImportError("Please install wgpu to use this renderer -> 'pip install wgpu'.")

from ada.visit.render_backend import RenderBackend, MeshInfo

BG_GRAY = Color(57, 57, 57)
PICKED_COLOR = Color(0, 123, 255)


class RendererPyGFX:
    def __init__(self, render_backend: RenderBackend, canvas_title: str = "PyGFX Renderer"):
        self.backend = render_backend

        self._mesh_map = {}
        self._selected_mat = gfx.MeshPhongMaterial(color=PICKED_COLOR, flat_shading=True)
        self.selected_mesh = None
        self.scene = gfx.Scene()
        self.scene.add(gfx.Background(None, gfx.BackgroundMaterial(BG_GRAY.hex)))
        self._scene_objects = gfx.Group()
        self.scene.add(self._scene_objects)

        canvas = WgpuCanvas(title=canvas_title, max_fps=60)
        renderer = gfx.renderers.WgpuRenderer(canvas, show_fps=False)
        # window = glfw.create_window(int(600), int(400), "GlfW", None, None)
        # glfw.make_context_current(window)
        self.display = gfx.Display(canvas=canvas, renderer=renderer)
        self.on_click_pre: Callable[[gfx.PointerEvent], None] | None = None
        self.on_click_post: Callable[[gfx.PointerEvent, MeshInfo], None] | None = None
        self._init_scene()

    def _init_scene(self):
        scene = self.scene
        scene.add(gfx.DirectionalLight())
        scene.add(gfx.AmbientLight())
        scene.add(gfx_utils.GridHelper())
        scene.add(gfx_utils.AxesHelper())

    def _get_scene_meshes(self, scene: trimesh.Scene, tag: str) -> Iterable[gfx.Mesh]:
        for key, m in scene.geometry.items():
            mesh = gfx.Mesh(gfx_utils.geometry_from_mesh(m), material=gfx_utils.tri_mat_to_gfx_mat(m.visual.material))
            buffer_id = int(float(key.replace("node", "")))
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
        # raise NotImplementedError()

    def add_part(self, part: Part, render_override: dict[str, GeomRepr] = None):
        graph = part.get_graph_store()
        scene = trimesh.Scene(base_frame=graph.top_level.name)
        scene.metadata["meta"] = graph.create_meta(suffix="")
        bt = BatchTessellator()
        shapes_tess_iter = bt.batch_tessellate(part.get_all_physical_objects(), render_override=render_override)
        all_shapes = sorted(shapes_tess_iter, key=lambda x: x.material)
        for mat_id, meshes in groupby(all_shapes, lambda x: x.material):
            merged_store = concatenate_stores(meshes)
            merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, graph)

        self.add_trimesh_scene(scene, part.name, commit=True)

    def add_trimesh_scene(self, trimesh_scene: trimesh.Scene, tag: str, commit: bool = False):
        meshes = self._get_scene_meshes(trimesh_scene, tag)
        self._scene_objects.add(*meshes)
        self.backend.add_metadata(trimesh_scene.metadata, tag)
        if commit:
            self.backend.commit()

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

        if event.button != 1:
            return

        if "face_index" not in info:
            if self.selected_mesh is not None:
                self.scene.remove(self.selected_mesh)
            return

        face_index = info["face_index"] * 3  # Backend uses a flat array of indices
        mesh: gfx.Mesh = event.target

        # Get what face was clicked
        res = self._mesh_map.get(mesh.id, None)
        if res is None:
            print("Could not find mesh id in map")
            return
        glb_fname, buffer_id = res

        mesh_data = self.backend.get_mesh_data_from_face_index(face_index, buffer_id, glb_fname)

        if self.selected_mesh is not None:
            self.scene.remove(self.selected_mesh)

        s = mesh_data.start // 3
        e = mesh_data.end // 3 + 1
        indices = mesh.geometry.indices.data[s:e]
        self.selected_mesh = clicked_mesh(mesh, indices, self._selected_mat)

        self.scene.add(self.selected_mesh)

        if self.on_click_post is not None:
            self.on_click_post(event, mesh_data)
        else:
            coord = np.array(event.pick_info["face_coord"])
            print(mesh_data, coord)

    def _add_event_handlers(self):
        ob = self._scene_objects
        ob.add_event_handler(self.on_click, "pointer_down")

    def show(self):
        self._add_event_handlers()
        self.display.show(self.scene)


def clicked_mesh(mesh: gfx.Mesh, indices, material, sfac=1.01) -> gfx.Mesh:
    trim = trimesh.Trimesh(vertices=mesh.geometry.positions.data, faces=indices)
    scale_tri_mesh(trim, sfac)

    geom = gfx.Geometry(
        positions=np.ascontiguousarray(trim.vertices, dtype="f4"),
        indices=np.ascontiguousarray(trim.faces, dtype="i4"),
    )

    c_mesh = gfx.Mesh(geom, material)
    c_mesh.scale.set(sfac, sfac, sfac)
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
