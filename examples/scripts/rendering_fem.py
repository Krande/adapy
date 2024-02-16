import pathlib

import ada
from ada.config import logger

logger.setLevel("INFO")
_ROOT_DIR = pathlib.Path(__file__).parent.parent.parent
FEM_FILES = _ROOT_DIR / "files" / "fem_files"
CANTILEVER_DIR = FEM_FILES / "cantilever" / "code_aster"
CA_DIR = FEM_FILES / "code_aster"


def cantilever():
    rmed_file = CANTILEVER_DIR / "eigen_shell_cantilever_code_aster.rmed"

    rmed = ada.from_fem_res(rmed_file)
    rmed.show(new_glb_file="temp/eigen_shell_cantilever_code_aster.glb", server_args=["--debug"])


def portal_stru():
    portal = CA_DIR / "portal_01.rmed"
    rmed = ada.from_fem_res(r"C:\AibelProgs\downloads\ca_param_model_ca.rmed")
    rmed.show(new_glb_file="temp/portal_01.glb", server_args=["--debug"], update_only=True, warp_scale=5)


if __name__ == "__main__":
    # cantilever()
    portal_stru()
