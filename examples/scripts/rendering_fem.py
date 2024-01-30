import pathlib

import ada
from ada.config import logger

logger.setLevel("INFO")
_ROOT_DIR = pathlib.Path(__file__).parent.parent.parent
EX_DIR = _ROOT_DIR / "files" / "fem_files" / "cantilever" / "code_aster"


def main():
    rmed_file = EX_DIR / "eigen_shell_cantilever_code_aster.rmed"

    rmed = ada.from_fem_res(rmed_file)
    rmed.show(new_glb_file="temp/eigen_shell_cantilever_code_aster.glb", server_args=["--debug"])


if __name__ == "__main__":
    main()
