# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#

import ada
from ada.visit.render_backend import SqLiteBackend
from ada.visit.render_pygfx import RendererPyGFX


def main():
    bm1 = ada.Beam("my_beam_x", (2, 0, 0), (2, 0, 1), "IPE300", color="red")
    bm2 = ada.Beam("my_beam_y", (0, 2, 0), (0, 2, 1), "IPE300", color="blue")
    bm3 = ada.Beam("my_beam_z", (0, 0, 2), (0, 0, 3), "IPE300", color="green")

    bm4 = ada.Beam("my_beam_shell", (1, 1, 0), (1, 1, 1), "IPE300", color="yellow")

    box1 = ada.PrimBox("box1", (1, 0, 0), (1.5, 0.5, 0.5), color="red")

    p = ada.Assembly() / (ada.Part("MyBeam") / (bm1, bm2, bm3, box1))

    render = RendererPyGFX(render_backend=SqLiteBackend("temp/meshes.db"))
    render.add_part(p)
    render.add_geom(bm4.shell_geom(), bm4.name, bm4.guid)
    render.show()


if __name__ == "__main__":
    main()
