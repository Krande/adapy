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
    print("ds")


def test_read_static_abaqus_results(cantilever_dir):
    _ = read_odb_pckle_file(cantilever_dir / "abaqus/static_cantilever_abaqus.pckle")
    print("ds")
