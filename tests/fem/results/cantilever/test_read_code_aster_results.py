from ada.fem.formats.code_aster.results.read_rmed_results import read_rmed_file


def test_read_static_line_results(cantilever_dir):
    results = read_rmed_file(cantilever_dir / "code_aster/static_line_cantilever_code_aster.rmed")
    assert len(results.results) == 1

    # results.to_gltf("temp/ca_line.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)


def test_read_static_shell_results(cantilever_dir):
    results = read_rmed_file(cantilever_dir / "code_aster/static_shell_cantilever_code_aster.rmed")
    assert len(results.results) == 1

    # results.to_gltf("temp/ca_shell.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)


def test_read_static_solid_results(cantilever_dir):
    results = read_rmed_file(cantilever_dir / "code_aster/static_solid_cantilever_code_aster.rmed")
    assert len(results.results) == 1

    # results.to_gltf("temp/ca_solid.glb", 1, 'result__DEPL', warp_field='result__DEPL', warp_step=1, warp_scale=10)
