# pip install -U pygfx glfw

import pathlib
from typing import Iterable

import numpy as np
import trimesh.visual.material

try:
    import pygfx as gfx
except ImportError:
    raise ImportError("Please install pygfx to use this renderer -> 'pip install pygfx'.")
try:
    from wgpu.gui.auto import WgpuCanvas
except ImportError:
    raise ImportError("Please install wgpu to use this renderer -> 'pip install wgpu'.")

from ada.visit.render_backend import RenderBackend


def tri_mat_to_gfx_mat(tri_mat: trimesh.visual.material.PBRMaterial) -> gfx.MeshPhongMaterial | gfx.MeshBasicMaterial:
    color = gfx.Color(*[x / 255 for x in tri_mat.baseColorFactor[:3]])
    return gfx.MeshPhongMaterial(color=color, flat_shading=True)


def geometry_from_trimesh(mesh):
    """Convert a Trimesh geometry object to pygfx geometry."""
    from trimesh import Trimesh  # noqa

    if not isinstance(mesh, Trimesh):
        raise NotImplementedError()

    kwargs = dict(
        positions=np.ascontiguousarray(mesh.vertices, dtype="f4"),
        indices=np.ascontiguousarray(mesh.faces, dtype="i4"),
        # normals=np.ascontiguousarray(mesh.vertex_normals, dtype="f4"),
    )
    if mesh.visual.kind == "texture" and mesh.visual.uv is not None and len(mesh.visual.uv) > 0:
        # convert the uv coordinates from opengl to wgpu conventions.
        # wgpu uses the D3D and Metal coordinate systems.
        # the coordinate origin is in the upper left corner, while the opengl coordinate
        # origin is in the lower left corner.
        # trimesh loads textures according to the opengl coordinate system.
        wgpu_uv = mesh.visual.uv * np.array([1, -1]) + np.array([0, 1])  # uv.y = 1 - uv.y
        kwargs["texcoords"] = np.ascontiguousarray(wgpu_uv, dtype="f4")
    elif mesh.visual.kind == "vertex":
        kwargs["colors"] = np.ascontiguousarray(mesh.visual.vertex_colors, dtype="f4")

    return gfx.Geometry(**kwargs)


class RendererPyGFX:
    def __init__(self, render_backend: RenderBackend):
        self.backend = render_backend
        self.scene = gfx.Scene()
        self._pick_objects: gfx.Group = gfx.Group()
        self.scene.add(self._pick_objects)
        self._mesh_map = {}

        self._init_scene()

    def _init_scene(self):
        scene = self.scene
        scene.add(gfx.DirectionalLight())
        scene.add(gfx.AmbientLight())
        scene.add(gfx.GridHelper())
        scene.add(gfx.AxesHelper(size=40, thickness=5))

    def _trimesh_scene_to_mesh(self, glb_file: pathlib.Path) -> Iterable[gfx.Mesh]:
        scene = self.backend.add_glb(glb_file, commit=False)

        for node_name in scene.graph.nodes_geometry:
            transform, geometry_name = scene.graph[node_name]
            current = scene.geometry[geometry_name]
            current.apply_transform(transform)

        for key, m in scene.geometry.items():
            mesh = gfx.Mesh(geometry_from_trimesh(m), tri_mat_to_gfx_mat(m.visual.material))
            buffer_id = int(float(key.replace("node", "")))
            self._mesh_map[mesh.id] = (glb_file.stem, buffer_id)
            yield mesh

    def _import_glb_data(self, glb_files: Iterable[pathlib.Path]):
        num_scenes = 0
        num_meshes = 0

        for glb_file in glb_files:
            num_scenes += 1
            for mesh in self._trimesh_scene_to_mesh(glb_file):
                yield mesh
                num_meshes += 1
        print(f"Loaded {num_meshes} meshes from {num_scenes} glb files")

    def load_glb_files_into_scene(self, glb_files: Iterable[pathlib.Path]):
        meshes = self._import_glb_data(glb_files)
        self._pick_objects.add(*meshes)
        self.backend.commit()

    def _add_event_handlers(self):
        selected_mat = gfx.MeshPhongMaterial(color="#ff0000", flat_shading=True)
        ob = self._pick_objects
        selected_mesh = None
        sfac = 1.0001

        @ob.add_event_handler("pointer_down", "pointer_up", "button=1")
        def offset_point(event: gfx.PointerEvent):
            nonlocal selected_mesh
            info = event.pick_info
            if "face_index" not in info:
                return

            face_index = info["face_index"]
            mesh: gfx.Mesh = event.target

            # Get what face was clicked
            res = self._mesh_map.get(mesh.id, None)
            if res is None:
                print("Could not find mesh id in map")
                return
            glb_fname, buffer_id = res

            mesh_data = self.backend.get_mesh_data_from_face_index(face_index, buffer_id, glb_fname)
            indices = mesh.geometry.indices.data[mesh_data.start: mesh_data.end]
            geom = gfx.Geometry(positions=mesh.geometry.positions.data, indices=indices)
            if selected_mesh is not None:
                self.scene.remove(selected_mesh)
            selected_mesh = gfx.Mesh(geom, selected_mat)

            selected_mesh.scale.set(sfac, sfac, sfac)
            self.scene.add(selected_mesh)
            print(mesh_data)

    def show(self):
        canvas = WgpuCanvas(title="PyGFX example")
        renderer = gfx.renderers.WgpuRenderer(canvas, show_fps=False)

        bbox = self._pick_objects.children[0].geometry.bounding_box()
        print(bbox)

        self._add_event_handlers()

        gfx.show(self.scene, renderer=renderer)  # , before_render=animate)
