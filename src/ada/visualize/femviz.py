import logging
import os
import traceback
from dataclasses import dataclass

import meshio
import numpy as np
from pythreejs import Group

from ada.fem import FEM
from ada.fem.shapes import ElemShape
from ada.fem.shapes import definitions as shape_def

from .threejs_utils import edges_to_mesh, faces_to_mesh, vertices_to_mesh
from .utils import get_edges_from_fem, get_faces_from_fem, get_vertices_from_fem


@dataclass
class ViewItem:
    fem: FEM
    vertices: np.array
    edges: np.array
    faces: np.array


class BBox:
    max: list
    min: list
    center: list


def fem_to_mesh(
    fem: FEM, face_colors=None, vertex_colors=(8, 8, 8), edge_color=(8, 8, 8), edge_width=1, vertex_width=1
):
    vertices, faces, edges = get_vertices_from_fem(fem), get_faces_from_fem(fem), get_edges_from_fem(fem)

    name = fem.name

    vertices_m = vertices_to_mesh(f"{name}_vertices", vertices, vertex_colors, vertex_width)
    edges_m = edges_to_mesh(f"{name}_edges", vertices, edges, edge_color=edge_color, linewidth=edge_width)
    faces_mesh = faces_to_mesh(f"{name}_faces", vertices, faces, colors=face_colors)

    return vertices_m, edges_m, faces_mesh


class FemRenderer:
    def __init__(self):
        self._view_items = []
        self._meshes = []

        # the group of 3d and 2d objects to render
        self._displayed_pickable_objects = Group()

    def add_fem(self, fem: FEM):
        vertices, faces, edges = get_vertices_from_fem(fem), get_faces_from_fem(fem), get_edges_from_fem(fem)
        self._view_items.append(ViewItem(fem, vertices, edges, faces))

    def to_mesh(self):
        for vt in self._view_items:
            self._view_to_mesh(vt)

    def _view_to_mesh(
        self,
        vt: ViewItem,
        face_colors=None,
        vertex_colors=(8, 8, 8),
        edge_color=(8, 8, 8),
        edge_width=1,
        vertex_width=1,
    ):
        fem = vt.fem
        vertices = vt.vertices
        edges = vt.edges
        faces = vt.faces

        vertices_m = vertices_to_mesh(f"{fem.name}_vertices", vertices, vertex_colors, vertex_width)
        edges_m = edges_to_mesh(f"{fem.name}_edges", vertices, edges, edge_color=edge_color, linewidth=edge_width)
        face_geom, faces_m = faces_to_mesh(f"{fem.name}_faces", vertices, faces, colors=face_colors)

        return vertices_m, edges_m, faces_m

    def get_bounding_box(self):
        bounds = np.asarray([get_bounding_box(m) for m in self._meshes], dtype="float32")
        mi, ma = np.min(bounds, 0), np.max(bounds, 0)
        center = (mi + ma) / 2
        return mi, ma, center


def get_edges_and_faces_from_meshio(mesh: meshio.Mesh):
    from ada.fem.formats.mesh_io.common import meshio_to_ada

    edges = []
    faces = []
    for cell_block in mesh.cells:
        el_type = meshio_to_ada[cell_block.type]
        for elem in cell_block.data:
            elem_shape = ElemShape(el_type, elem)
            edges += elem_shape.edges
            if isinstance(elem_shape.type, shape_def.LineShapes):
                continue
            faces += elem_shape.faces
    return edges, faces


def get_bounding_box(vertices):
    return np.min(vertices, 0), np.max(vertices, 0)


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)


def visualize_it(res_file, temp_dir=".temp", default_index=0):
    import pathlib

    import meshio
    from ipygany import ColorBar, IsoColor, PolyMesh, Scene, Warp, colormaps
    from IPython.display import clear_output, display
    from ipywidgets import AppLayout, Dropdown, FloatSlider, VBox, jslink

    from ada.core.vector_utils import vector_length

    res_file = pathlib.Path(res_file).resolve().absolute()
    suffix = res_file.suffix.lower()

    suffix_map = {".rmed": "med", ".vtu": None}

    imesh = meshio.read(res_file, file_format=suffix_map[suffix])
    imesh.point_data = {key.replace(" ", "_"): value for key, value in imesh.point_data.items()}

    def filter_keys(var):
        if suffix == ".vtu" and var != "U":
            return False
        if suffix == ".rmed" and var == "point_tags":
            return False
        return True

    warp_data = [key for key in filter(filter_keys, imesh.point_data.keys())]
    magn_data = []
    for d in warp_data:
        res = [vector_length(v[:3]) for v in imesh.point_data[d]]
        res_norm = [r / max(res) for r in res]
        magn_data_name = f"{d}_magn"
        imesh.point_data[magn_data_name] = np.array(res_norm, dtype=np.float64)
        magn_data.append(magn_data_name)

    imesh.field_data = {key: np.array(value) for key, value in imesh.field_data.items()}

    tf = (pathlib.Path(temp_dir).resolve().absolute() / res_file.name).with_suffix(".vtu")

    if tf.exists():
        os.remove(tf)
    os.makedirs(tf.parent, exist_ok=True)
    imesh.write(tf)

    mesh = PolyMesh.from_vtk(str(tf))
    mesh.default_color = "gray"

    warp_vec = warp_data[default_index]
    try:
        colored_mesh = IsoColor(mesh, input=magn_data[default_index], min=0.0, max=1.0)
    except KeyError as e:
        trace_str = traceback.format_exc()
        logging.error(f'KeyError "{e}"\nTrace: "{trace_str}"')
        colored_mesh = mesh
    except ImportError as e:
        trace_str = traceback.format_exc()
        logging.error("This might be")
        logging.error(f'ImportError "{e}"\nTrace: "{trace_str}"')
        return

    warped_mesh = Warp(colored_mesh, input=warp_vec, warp_factor=0.0)

    warp_slider = FloatSlider(value=0.0, min=-1.0, max=1.0)

    jslink((warped_mesh, "factor"), (warp_slider, "value"))

    # Create a colorbar widget
    colorbar = ColorBar(colored_mesh)

    # Colormap choice widget
    colormap = Dropdown(options=colormaps, description="colormap:")

    jslink((colored_mesh, "colormap"), (colormap, "index"))

    # EigenValue choice widget
    eig_map = Dropdown(options=warp_data, description="Data Value:")

    scene = Scene([warped_mesh])
    app = AppLayout(
        left_sidebar=scene, right_sidebar=VBox((eig_map, warp_slider, colormap, colorbar)), pane_widths=[2, 0, 1]
    )

    def change_input(change):
        vec_name = change["new"]
        logging.info(vec_name)
        colored_mesh.input = vec_name + "_magn"
        warped_mesh.input = vec_name
        # Highly inefficient but likely needed due to bug https://github.com/QuantStack/ipygany/issues/69
        clear_output()
        display(app)

    eig_map.observe(change_input, names=["value"])

    return app
