import pathlib

import ada
from ada.config import logger

logger.setLevel("INFO")
_ROOT_DIR = pathlib.Path(__file__).parent.parent.parent
FEM_FILES = _ROOT_DIR / "files" / "fem_files"
CANTILEVER_DIR = FEM_FILES / "cantilever" / "code_aster"
CA_DIR = FEM_FILES / "code_aster"


def cantilever():
    rmed_file = CA_DIR / "Cantilever_CA_EIG_sh.rmed"

    rmed = ada.from_fem_res(rmed_file)
    rmed.show()


if __name__ == "__main__":
    cantilever()
