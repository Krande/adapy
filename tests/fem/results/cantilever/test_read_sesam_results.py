from ada.fem.formats.sesam.results.read_sif import read_sif_file


def test_read_static_shell_results(cantilever_dir):
    results = read_sif_file(cantilever_dir / "sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIF")
    results.to_gltf("temp/sesam.glb", 1, "RVNODDIS", warp_field="RVNODDIS", warp_step=1, warp_scale=10)


def test_ec3_code_check_results(cantilever_dir):
    # UfTot -> 0.68 uf654
    from ada.core.utils import traverse_hdf_datasets

    traverse_hdf_datasets(cantilever_dir / "sesam/static/Eurocode3_Loads1.h5")
    traverse_hdf_datasets(cantilever_dir / "sesam/static/Eurocode31.h5")
