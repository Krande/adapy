import pathlib

import ada
from ada.visit.renderer_manager import RenderParams

fem_files = pathlib.Path(__file__).parent.parent.parent.parent / "files/fem_files"


def view_it(num_elem=1):
    if num_elem == 1:
        elem = "1EL_SHELL_R1"
    elif num_elem == 2:
        elem = "2EL_SHELL_R1"
    else:
        raise NotImplementedError("Only 1 or 2 elements are supported")

    a = ada.from_fem(fem_files / f"sesam/{elem}.SIF")

    p = a.get_part("T1")
    params = RenderParams(gltf_export_to_file="temp/simple_mesh.glb")
    p.fem.show(params_override=params)


if __name__ == "__main__":
    view_it(2)
