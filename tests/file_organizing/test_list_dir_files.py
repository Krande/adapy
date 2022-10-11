import pathlib

from ada.core.file_system import get_list_of_files


def test_list_of_files(example_files):
    list_of_files = get_list_of_files(example_files / "fem_files/meshes")

    # Only check the first 10 elements of the list
    desired_list = [
        "abaqus/element_elset.inp",
        "abaqus/nle1xf3c.inp",
        "abaqus/README.md",
        "abaqus/UUea.inp",
        "flac3d/flac3d_mesh_ex.f3grid",
        "flac3d/flac3d_mesh_ex_bin.f3grid",
        "med/box.med",
        "med/cylinder.med",
        "med/README.md",
        "medit/cube86.mesh",
    ]
    for p_actual, p_desired in zip(list_of_files, desired_list):
        pa = pathlib.Path(p_actual).resolve().absolute()
        pd = (pathlib.Path(__file__).parent / "../../files/fem_files/meshes" / p_desired).resolve().absolute()
        assert pa == pd
