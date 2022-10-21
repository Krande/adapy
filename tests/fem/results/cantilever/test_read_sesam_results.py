import os

from ada.fem.formats.sesam.results.read_sif import read_sif_file


def test_read_static_shell_results(cantilever_dir):
    results = read_sif_file(cantilever_dir / "sesam/static/STATIC_CANTILEVER_SESAMR1.SIF")
    m = results.to_meshio_mesh()
    os.makedirs("temp", exist_ok=True)
    m.write("temp/sesam.vtu")


def test_ec3_code_check_results(cantilever_dir):
    # UfTot -> 0.68 uf654
    from ada.core.utils import traverse_hdf_datasets

    traverse_hdf_datasets(cantilever_dir / "sesam/static/Eurocode3_Loads1.h5")
    traverse_hdf_datasets(cantilever_dir / "sesam/static/Eurocode31.h5")
