from ada.fem.meshing import GmshOptions


def main():
    from conftest import beam
    from test_fem_eig_cantilever import test_fem_eig

    test_fem_eig(
        beam(),
        "abaqus",
        "solid",
        2,
        False,
        short_name_map=None,
        overwrite=True,
        execute=True,
        eigen_modes=None,
        name="debug",
        debug=True,
        interactive=True,
        silent=False,
        options=GmshOptions(Mesh_ElementOrder=2, Mesh_Algorithm=6, Mesh_Algorithm3D=1),
        perform_quality_check=True,
    )


if __name__ == "__main__":
    main()
