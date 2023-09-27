# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#

import ada
from ada.geom.points import Point
from ada.visit.render_backend import MeshInfo, SqLiteBackend
from ada.visit.render_pygfx import RendererPyGFX


def main():
    objects = []
    render_override = {}

    po = [Point(1, 1, 3) + x for x in [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)]]
    pipe1 = ada.Pipe("pipe1", po, "PIPE200x5", color="green")

    objects += [pipe1]

    a = ada.Assembly() / objects
    a.to_stp("temp/part.stp", geom_repr_override=render_override)
    a.to_ifc("temp/part.ifc", geom_repr_override=render_override)

    render = RendererPyGFX(render_backend=SqLiteBackend("temp/meshes.db"))

    def _on_click(event, mesh_data: MeshInfo):
        obj = a.get_by_guid(mesh_data.mesh_id)
        print(obj)

    render.on_click_post = _on_click
    render.add_part(a, render_override=render_override)
    render.show()


if __name__ == "__main__":
    main()
