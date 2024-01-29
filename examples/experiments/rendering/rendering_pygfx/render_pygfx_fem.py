# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#

import ada
from ada.config import logger
from ada.visit.render_backend import MeshInfo, SqLiteBackend
from ada.visit.rendering.render_pygfx import RendererPyGFX

logger.setLevel("INFO")


def main():
    objects = []
    render_override = {}
    bm1 = ada.Beam("my_beam_x", (2, 0, 0), (2, 0, 1), "IPE300", color="red")
    bm1.add_boolean(ada.PrimCyl("cyl2", (1.8, 0, 0.5), (2.2, 0, 0.5), 0.1))

    p = ada.Part("MyBeam")
    p.fem = bm1.to_fem_obj(0.05, "shell", use_quads=True)

    ada.Plate("plate1", [(0, 0), (1, 0), (0, 1)], 0.1, origin=(0, 0, 0.5), color="blue", parent=p)
    # p.fem = pl.to_fem_obj(1, "shell", use_quads=True)

    a = ada.Assembly() / (p / objects)

    a.to_fem("cutout_bm_aba", "abaqus", scratch_dir="temp", overwrite=True)
    a.to_fem("cutout_bm_ses", "sesam", scratch_dir="temp", overwrite=True)
    a.to_fem("cutout_bm_ufo", "usfos", scratch_dir="temp", overwrite=True)
    a.to_gltf("temp/beam.glb")

    render = RendererPyGFX(render_backend=SqLiteBackend())

    def _on_click(event, mesh_data: MeshInfo):
        print(mesh_data, event.pick_info)

    render.on_click_post = _on_click
    render.add_part(a, render_override=render_override)
    render.show()


if __name__ == "__main__":
    main()
