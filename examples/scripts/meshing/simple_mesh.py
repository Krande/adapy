import ada

import pathlib

from ada.visit.renderer_manager import RenderParams

fem_files = pathlib.Path(__file__).parent.parent.parent.parent / "files/fem_files"


def view_it():
    a = ada.from_fem(fem_files / "sesam/1EL_SHELL_R1.SIF")
    p = a.get_part("T1")
    params = RenderParams(gltf_export_to_file="temp/sesam_1el_sh.glb")
    p.fem.show(params_override=params)


if __name__ == "__main__":
    view_it()
