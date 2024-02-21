from ada.config import logger
from ada.fem.meshing import GmshOptions


def main():
    from conftest import beam
    from test_fem_eig_cantilever import test_fem_eig

    test_fem_eig(
        beam(),
        "calculix",
        "solid",
        2,
        False,
        short_name_map=None,
        overwrite=True,
        execute=True,
        eigen_modes=10,
        name="debug",
        debug=True,
        interactive=False,
        silent=False,
        options=GmshOptions(Mesh_ElementOrder=2, Mesh_Algorithm=6, Mesh_Algorithm3D=10),
        perform_quality_check=True,
    )


if __name__ == "__main__":
    logger.setLevel("INFO")
    main()
