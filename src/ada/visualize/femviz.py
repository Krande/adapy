import os
import traceback

import meshio
import numpy as np

from ada.config import get_logger
from ada.fem.shapes import ElemShape
from ada.fem.shapes import definitions as shape_def

logger = get_logger()


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
        logger.error(f'KeyError "{e}"\nTrace: "{trace_str}"')
        colored_mesh = mesh
    except ImportError as e:
        trace_str = traceback.format_exc()
        logger.error("This might be")
        logger.error(f'ImportError "{e}"\nTrace: "{trace_str}"')
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
        logger.info(vec_name)
        colored_mesh.input = vec_name + "_magn"
        warped_mesh.input = vec_name
        # Highly inefficient but likely needed due to bug https://github.com/QuantStack/ipygany/issues/69
        clear_output()
        display(app)

    eig_map.observe(change_input, names=["value"])

    return app
