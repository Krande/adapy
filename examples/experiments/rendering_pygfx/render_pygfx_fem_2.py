# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#
import meshio
import pathlib

import ada
from ada.config import logger

logger.setLevel("INFO")
_ROOT_DIR = pathlib.Path(__file__).parent.parent.parent.parent
EX_DIR = _ROOT_DIR / "files" / "fem_files" / "cantilever" / "code_aster"


def main():
    rmed_file = EX_DIR / "eigen_shell_cantilever_code_aster.rmed"
    mesh = meshio.read(rmed_file, file_format="med")
    rmed = ada.from_fem_res(rmed_file)
    rmed.to_xdmf("temp/eigen_shell_cantilever_code_aster.xdmf")
    rmed.to_viewer(1, 'modes___DEPL[0] - 13.5363')


if __name__ == "__main__":
    main()
