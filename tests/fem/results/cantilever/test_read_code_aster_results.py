from ada.fem.formats.code_aster.results.read_rmed_results import read_rmed_file


def test_read_static_line_results(cantilever_dir):
    results = read_rmed_file(cantilever_dir / "code_aster/static_line_cantilever_code_aster.rmed")
    assert len(results.results) == 2


def test_read_static_shell_results(cantilever_dir):
    results = read_rmed_file(cantilever_dir / "code_aster/static_shell_cantilever_code_aster.rmed")
    assert len(results.results) == 2


def test_read_static_solid_results(cantilever_dir):
    results = read_rmed_file(cantilever_dir / "code_aster/static_solid_cantilever_code_aster.rmed")
    assert len(results.results) == 2
