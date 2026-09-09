from ada.fem.formats.sesam.results.read_sif import read_sif_file


def _names(result):
    return {r.name for r in result.results}


def test_read_shell_1el(fem_files):
    result = read_sif_file(fem_files / "sesam/1EL_SHELL_R1.SIF")
    names = _names(result)
    # The raw records survive the read unchanged...
    assert {"RVNODDIS", "STRESS"} <= names
    # ...and the semantic derived fields are added alongside them.
    assert any(n.startswith("sesam.") for n in names)


def test_read_shell_2el(fem_files):
    result = read_sif_file(fem_files / "sesam/2EL_SHELL_R1.SIF")
    names = _names(result)
    assert {"RVNODDIS", "STRESS"} <= names
    assert any(n.startswith("sesam.") for n in names)
