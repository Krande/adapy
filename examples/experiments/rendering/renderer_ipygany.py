from ipygany import PolyMesh

from .new_render_api import Visualize


def mesh_from_arrays(vertices, faces):
    return PolyMesh(vertices=vertices, triangle_indices=faces)


def render_ipyany_scene(visualize: Visualize, off_screen_file=False):
    from ipygany import Scene

    meshes = []
    for obj in visualize.objects:
        meshes.append(obj.convert_to_ipygany_mesh())
    return Scene(meshes)
