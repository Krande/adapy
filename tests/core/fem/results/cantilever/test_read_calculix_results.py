import pytest

import ada
from ada.fem.formats.calculix.results.read_frd_file import ReadFrdFailedException


def test_read_static_line_calculix_results(cantilever_dir):
    with pytest.raises(ReadFrdFailedException):
        _ = ada.from_fem_res(cantilever_dir / "calculix/static_line_cantilever_calculix.frd")


def test_read_static_shell_calculix_results(cantilever_dir):
    res = ada.from_fem_res(cantilever_dir / "calculix/static_shell_cantilever_calculix.frd")
    assert len(res.results) == 8

    # res.to_gltf("temp/ccx_model_sh.glb", 2, "DISP", warp_field="DISP", warp_step=2, warp_scale=10)


def test_read_static_solid_calculix_results(cantilever_dir):
    res = ada.from_fem_res(cantilever_dir / "calculix/static_solid_cantilever_calculix.frd")
    assert len(res.results) == 8

    # res.to_gltf("temp/ccx_model_so.glb", 2, "DISP", warp_field="DISP", warp_step=2, warp_scale=10)


def test_read_eig_shell_calculix_results(cantilever_dir):
    res = ada.from_fem_res(cantilever_dir / "calculix/eigen_shell_cantilever_calculix.frd")
    eig_data = res.get_eig_summary()
    assert len(eig_data.modes) == 20
