from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file_proto


def test_read_static_shell_calculix_results(cantilever_dir):
    res = read_from_frd_file_proto(cantilever_dir / "calculix/static_shell_cantilever_calculix.frd")
    res.to_gltf("temp/ccx_model.glb", 2, "DISP", warp_field="DISP", warp_step=2, warp_scale=10)
