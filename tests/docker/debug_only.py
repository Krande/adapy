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
    )


if __name__ == "__main__":
    main()
