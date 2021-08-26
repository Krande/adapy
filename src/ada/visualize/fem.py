import logging
import os
import traceback
from dataclasses import dataclass

import numpy as np
from pythreejs import Group

from ..fem import FEM
from .threejs_geom import edges_to_mesh, faces_to_mesh, vertices_to_mesh


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
        self, vt, face_colors=None, vertex_colors=(8, 8, 8), edge_color=(8, 8, 8), edge_width=1, vertex_width=1
    ):
        """

        :param vt:
        :type vt: ViewItem
        :return:
        """
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


def get_edges_and_faces_from_meshio(mesh):
    """

    :param mesh:
    :type mesh: meshio.Mesh
    :return:
    """
    from ada.fem.io.io_meshio import meshio_to_ada_type
    from ada.fem.shapes import ElemShapes

    edges = []
    faces = []
    for cell_block in mesh.cells:
        el_type = meshio_to_ada_type[cell_block.type]
        for elem in cell_block.data:
            res = ElemShapes(el_type, elem)
            edges += res.edges
            if res.type in res.beam:
                continue
            faces += res.faces
    return edges, faces


def get_faces_from_fem(fem, convert_bm_to_shell=False):
    """

    :param fem:
    :param convert_bm_to_shell: Converts Beam elements to a shell element equivalent
    :type fem: ada.fem.FEM
    :return:
    :rtype: list
    """
    from ..fem.shapes import ElemShapes

    ids = []
    for el in fem.elements.elements:
        if ElemShapes.is_beam_elem(el):
            continue
        for f in el.shape.faces:
            # Convert to indices, not id
            ids += [[int(e.id - 1) for e in f]]
    return ids


def get_edges_from_fem(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    :rtype: list
    """
    ids = []
    for el in fem.elements.elements:
        for f in el.shape.edges_seq:
            # Convert to indices, not id
            ids += [[int(el.nodes[e].id - 1) for e in f]]
    return ids


def get_faces_for_bm_elem(elem):
    """

    :param elem:
    :type elem: ada.fem.Elem
    :return:
    """

    # if ElemShapes.beam


def get_vertices_from_fem(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """

    return np.asarray([n.p for n in fem.nodes.nodes], dtype="float32")


def get_bounding_box(vertices):
    return np.min(vertices, 0), np.max(vertices, 0)


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)


def visualize_it(res_file, file_format="med", temp_file="temp/foo.vtu", default_index=0):
    import pathlib

    import meshio
    from ipygany import ColorBar, IsoColor, PolyMesh, Scene, Warp, colormaps
    from IPython.display import clear_output, display
    from ipywidgets import AppLayout, Dropdown, FloatSlider, VBox, jslink

    from ada.core.utils import vector_length

    res_file = pathlib.Path(res_file).resolve().absolute()
    imesh = meshio.read(res_file, file_format=file_format)
    imesh.point_data = {key.replace(" ", "_"): value for key, value in imesh.point_data.items()}

    warp_data = [key for key in imesh.point_data.keys() if key != "point_tags"]
    magn_data = []
    for d in warp_data:
        res = [vector_length(v[:3]) for v in imesh.point_data[d]]
        res_norm = [r / max(res) for r in res]
        magn_data_name = f"{d}_magn"
        imesh.point_data[magn_data_name] = np.array(res_norm, dtype=np.float64)
        magn_data.append(magn_data_name)
    imesh.field_data = {key: np.array(value) for key, value in imesh.field_data.items()}
    tf = pathlib.Path(temp_file).resolve().absolute()
    if tf.exists():
        os.remove(tf)
    os.makedirs(tf.parent, exist_ok=True)
    imesh.write(tf)

    # assert isinstance(imesh, meshio.Mesh)
    # vertices = imesh.points
    # indices = imesh.cells

    mesh = PolyMesh.from_vtk(str(tf))
    mesh.default_color = "gray"

    # mesh = PolyMesh(
    #     vertices=vertices,
    #     triangle_indices=get_ugrid_triangles(grid),
    #     data=_grid_data_to_data_widget(get_ugrid_data(grid))
    # )

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
    eig_map = Dropdown(options=warp_data, description="Eigenvalue:")

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
