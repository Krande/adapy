import pytest

import ada


@pytest.fixture
def test_meshio_dir(test_dir):
    return test_dir / "meshio"


def test_read_write_code_aster_to_xdmf(example_files, test_meshio_dir):
    a = ada.from_fem(example_files / "fem_files/meshes/med/box.med", fem_converter="meshio")
    a.to_fem("box_xdmf", fem_format="xdmf", fem_converter="meshio", overwrite=True, scratch_dir=test_meshio_dir)


def test_read_write_code_aster_to_abaqus(example_files, test_meshio_dir):
    a = ada.from_fem(example_files / "fem_files/meshes/med/box.med", fem_converter="meshio")
    a.to_fem("box_abaqus", fem_format="abaqus", fem_converter="meshio", overwrite=True, scratch_dir=test_meshio_dir)


def test_read_C3D20(example_files):
    a = ada.from_fem(example_files / "fem_files/calculix/contact2e.inp", fem_converter="meshio")
    print(a)


def test_read_abaqus(example_files):
    b = ada.from_fem(example_files / "fem_files/meshes/abaqus/element_elset.inp", fem_converter="meshio")
    print(b)


def test_read_code_aster(example_files):
    a = ada.from_fem(example_files / "fem_files/meshes/med/box.med", fem_converter="meshio")
    print(a)
