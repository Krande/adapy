import pytest

import ada


def test_read_static_line_results(cantilever_dir):
    results = ada.from_fem_res(cantilever_dir / "code_aster/static_line_cantilever_code_aster.rmed")
    assert len(results.results) == 8

    # results.to_gltf("temp/ca_line.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)


def test_read_static_shell_results(cantilever_dir):
    results = ada.from_fem_res(cantilever_dir / "code_aster/static_shell_cantilever_code_aster.rmed")
    assert len(results.results) == 7

    # results.to_gltf("temp/ca_sh_stat.glb", -1, 'result__DEPL', warp_field='result__DEPL', warp_step=-1, warp_scale=10)


def test_read_static_solid_results(cantilever_dir):
    results = ada.from_fem_res(cantilever_dir / "code_aster/static_solid_cantilever_code_aster.rmed")
    assert len(results.results) == 5

    results.to_gltf("temp/ca_so_stat.glb", -1, "result__DEPL", warp_field="result__DEPL", warp_step=-1, warp_scale=10)


def test_read_eigen_line_results(cantilever_dir):
    results = ada.from_fem_res(cantilever_dir / "code_aster/eigen_line_cantilever_code_aster.rmed")
    assert len(results.results) == 20

    eig_data = results.get_eig_summary()
    assert len(eig_data.modes) == 20

    eigen_res = tuple([r.f_hz for r in eig_data.modes])
    eig_assert = (5.021624917924645, 12.979339119510962, 15.07864493168179)

    assert eigen_res[:3] == pytest.approx(eig_assert)

    # results.to_gltf("temp/ca_li_eig.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)


def test_read_eigen_shell_results(cantilever_dir):
    results = ada.from_fem_res(cantilever_dir / "code_aster/eigen_shell_cantilever_code_aster.rmed")
    assert len(results.results) == 20

    eig_data = results.get_eig_summary()
    assert len(eig_data.modes) == 20

    eigen_res = tuple([r.f_hz for r in eig_data.modes])
    eig_assert = (13.53625675820233, 20.32094643858448, 52.155714162868584)
    assert eigen_res[:3] == pytest.approx(eig_assert)

    # results.to_gltf("temp/ca_sh_eig.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)


def test_read_eigen_solid_results(cantilever_dir):
    results = ada.from_fem_res(cantilever_dir / "code_aster/eigen_solid_cantilever_code_aster.rmed")
    assert len(results.results) == 20

    eig_data = results.get_eig_summary()
    assert len(eig_data.modes) == 20

    eigen_res = tuple([r.f_hz for r in eig_data.modes])
    eig_assert = (14.275450399786994, 21.568363891531334, 51.08924173189885)
    assert eigen_res[:3] == pytest.approx(eig_assert)

    # results.to_gltf("temp/ca_so_eig.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)
