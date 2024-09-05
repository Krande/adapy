import pathlib

from ada.core.file_system import get_list_of_files


def test_list_of_files(example_files, fem_files):
    list_of_files = get_list_of_files(example_files / "fem_files")

    # Only check the first 10 elements of the list
    desired_list = [
        "abaqus/box.inp",
        "abaqus/box_rigid.inp",
        "abaqus/element_elset.inp",
        "abaqus/nle1xf3c.inp",
        "abaqus/README.md",
        "abaqus/UUea.inp",
        "calculix/contact2e.inp",
        "calculix/u1general.inp",
    ]
    for p_actual, p_desired in zip(list_of_files, desired_list):
        pa = pathlib.Path(p_actual).resolve().absolute()
        pd = (fem_files / p_desired).resolve().absolute()
        assert pa == pd
