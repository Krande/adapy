import os

import pytest

import ada
from ada.fem.formats.abaqus.results.read_odb import read_odb_pckle_file


@pytest.fixture
def cantilever_dir(fem_files):
    return fem_files / "cantilever"


def test_read_static_calculix_results(cantilever_dir):
    res = ada.from_fem_res(cantilever_dir / "calculix/static_cantilever_calculix.frd", proto_reader=False)
    vm = res.to_vis_mesh()
    vm.to_gltf("temp/model.glb")


def test_read_eigen_line_abaqus_results(cantilever_dir):
    odb_res = read_odb_pckle_file(cantilever_dir / "abaqus/eigen_line_cantilever_abaqus.pckle")
    steps = odb_res.get_steps()
    assert len(steps) == 21


def test_read_static_line_abaqus_results(cantilever_dir):
    _ = read_odb_pckle_file(cantilever_dir / "abaqus/static_line_cantilever_abaqus.pckle")
    print("ds")


def test_read_static_shell_abaqus_results(cantilever_dir):
    results = read_odb_pckle_file(cantilever_dir / "abaqus/static_shell_cantilever_abaqus.pckle")
    mesh = results.to_meshio_mesh()
    os.makedirs("temp", exist_ok=True)
    mesh.write("temp/test.vtu")
    print("ds")


def test_ec3_code_check_results(cantilever_dir):
    # UfTot -> 0.68 uf654
    from ada.core.utils import traverse_hdf_datasets

    traverse_hdf_datasets(cantilever_dir / "sesam/static/Eurocode3_Loads1.h5")
    traverse_hdf_datasets(cantilever_dir / "sesam/static/Eurocode31.h5")
