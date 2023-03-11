from ada.fem.formats.sesam.results.read_sif import read_sif_file


def test_read_shell_1el(fem_files):
    result = read_sif_file(fem_files / "sesam/1EL_SHELL_R1.SIF")
    assert len(result.results) == 2


def test_read_shell_2el(fem_files):
    result = read_sif_file(fem_files / "sesam/2EL_SHELL_R1.SIF")
    assert len(result.results) == 2
