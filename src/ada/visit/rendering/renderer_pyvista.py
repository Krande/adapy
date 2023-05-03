import numpy as np
import pyvista as pv

from .new_render_api import Visualize


def mesh_from_arrays(vertices, faces):
    # Convert Faces to input form required by pyvista
    np_faces2 = faces.reshape(int(len(faces) / 3), 3)
    res_ = np.full((len(np_faces2), 1), 3)
    faces_res = np.hstack((res_, np_faces2))

    return pv.PolyData(vertices, faces_res)


def render_ipyvista_scene(visualize: Visualize, backend="pythreejs", off_screen_file=None, **kwargs):
    off_screen_bool = True if off_screen_file is not None else False
    pl = pv.Plotter(off_screen=off_screen_bool)
    for viz_obj in visualize.objects:
        pl.add_mesh(
            viz_obj.convert_to_pyvista_mesh(),
            color=viz_obj.obj.colour,
            # style="surface",
            pbr=False,
            metallic=0.2,
        )
    pl.background_color = "white"
    pl.window_size = (980, 600)

    pl.enable_anti_aliasing()
    if off_screen_bool:
        pl.save_graphic(off_screen_file)
    return pl.show(jupyter_backend=backend, full_screen=False, **kwargs)
