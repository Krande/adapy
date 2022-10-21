from ada.fem.formats.abaqus.results.read_odb import read_odb_pckle_file
from ada.fem.formats.general import FEATypes


def test_read_eigen_line_abaqus_results(cantilever_dir):
    results = read_odb_pckle_file(cantilever_dir / "abaqus/eigen_line_cantilever_abaqus.pckle")
    steps = results.get_steps()
    assert len(steps) == 21


def test_read_static_line_abaqus_results(cantilever_dir):
    results = read_odb_pckle_file(cantilever_dir / "abaqus/static_line_cantilever_abaqus.pckle")
    assert len(results.results) == 36
    assert results.software == FEATypes.ABAQUS

    # results.to_gltf('temp/line_res.glb', 2, 'U')


def test_read_static_shell_abaqus_results(cantilever_dir):
    results = read_odb_pckle_file(cantilever_dir / "abaqus/static_shell_cantilever_abaqus.pckle")
    assert len(results.results) == 36
    assert results.software == FEATypes.ABAQUS

    results.to_gltf("temp/shell_res.glb", 2, "U")
