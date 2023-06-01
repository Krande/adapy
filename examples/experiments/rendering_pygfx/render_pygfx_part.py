# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#

import ada
from ada.base.types import GeomRepr
from ada.visit.render_backend import SqLiteBackend
from ada.visit.render_pygfx import RendererPyGFX


def main():
    bm1 = ada.Beam("my_beam_x", (2, 0, 0), (2, 0, 1), "IPE300", color="red")
    bm2 = ada.Beam("my_beam_y", (0, 2, 0), (0, 2, 1), "IPE300", color="green")
    bm3 = ada.Beam("my_beam_z", (0, 0, 2), (0, 0, 3), "IPE300", color="blue")

    bm4 = ada.Beam("my_beam_shell", (1, 1, 0), (1, 1, 1), "IPE300", color="yellow")
    bm5 = ada.Beam("my_beam_xyz_shell", (0, 1, 0), (1, 1, 1), "IPE300", color="yellow")
    bm5_so = ada.Beam("my_beam_xyz_solid", (0, 0, 0), (1, 0, 1), "IPE300", color="yellow")
    bm6 = ada.Beam("my_beam_xyz", (1, 2, 1), (1.5, 2.5, 1.5), "IPE300", color="yellow")

    render_override = {bm4.guid: GeomRepr.SHELL, bm5.guid: GeomRepr.SHELL}
    box1 = ada.PrimBox("box1", (1, 0, 0), (1.5, 0.5, 0.5), color="red")

    cyl1 = ada.PrimCyl("cyl1", (3, 0, 0), (3, 0.5, 0.5), 0.3, color="green")
    cone1 = ada.PrimCone("cone1", (4, 0, 0), (4, 0.5, 0.5), 0.3, color="green")
    sphere1 = ada.PrimSphere("sphere1", (5, 0, 0), 0.3, color="green")

    p = ada.Assembly() / (ada.Part("MyBeam") / (bm1, bm2, bm3, box1, bm4, bm5, bm6, bm5_so, cyl1, cone1, sphere1))
    p.to_stp('temp/part.stp', geom_repr_override=render_override)

    render = RendererPyGFX(render_backend=SqLiteBackend("temp/meshes.db"))
    render.add_part(p, render_override=render_override)
    render.show()


if __name__ == "__main__":
    main()
