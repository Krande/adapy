"""Result files through the CLI: ``ada view`` shows them as results, ``ada results`` summarises
and compares them.

The viewer itself is never started here: ``FEAResult.show`` is replaced by a recorder, so what
is pinned is which loader a file reaches and what reaches the viewer, not the viewer.
"""

from __future__ import annotations

import pytest

import ada_cli.main
from ada_cli.formats import RESULT_FORMATS, infer_result_format

_SIN = "fem_files/cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
_RMED_SHELL = "fem_files/cantilever/code_aster/static_shell_cantilever_code_aster.rmed"
_RMED_LINE = "fem_files/cantilever/code_aster/static_line_cantilever_code_aster.rmed"


@pytest.fixture
def shown(monkeypatch):
    """Every ``FEAResult.show`` call, as (result name, kwargs), instead of a viewer."""
    from ada.fem.results.common import FEAResult

    calls = []
    monkeypatch.setattr(FEAResult, "show", lambda self, **kw: calls.append((self.name, kw)))
    return calls


# ── the table ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,fmt",
    [
        ("run.SIN", "sesam"),
        ("run.sif", "sesam"),
        ("run.rmed", "code_aster"),
        ("run.frd", "calculix"),
        ("run.odb", "abaqus"),
        ("run.radanim", "opencourant"),
        # Models are not results, and neither is a .med mesh.
        ("model.FEM", None),
        ("model.med", None),
        ("model.inp", None),
        ("model", None),
    ],
)
def test_infer_result_format(path, fmt):
    assert infer_result_format(path) == fmt


@pytest.mark.parametrize("name", sorted(RESULT_FORMATS))
def test_every_result_format_is_one_the_library_post_processes(name):
    """The table is standard library only, so it restates names ``ada.from_fem_res`` takes; a
    name it does not know would fail only once someone opened such a file."""
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.utils import interpret_fem_format_from_path

    fea_type = FEATypes.from_str(name)
    for ext in RESULT_FORMATS[name]:
        inferred = interpret_fem_format_from_path(f"run.{ext}")
        # .odb is not in the library's own path inference, so the CLI passes the format.
        assert inferred in (fea_type, None), f".{ext}: the library reads it as {inferred}, the CLI as {name}"


# ── ada view ──────────────────────────────────────────────────────────────


def test_view_shows_a_result_file_as_results(example_files, shown, capsys):
    rc = ada_cli.main.main(["view", str(example_files / _SIN), "--ws-port", "9999", "--stats"])

    assert rc == 0
    assert shown == [("STATIC_SHELL_CANTILEVER_SESAMR1", {"renderer": "react", "host": "localhost", "port": 9999})]
    assert "Fields (" in capsys.readouterr().out


def test_view_from_reads_a_result_file_as_its_mesh(example_files, monkeypatch, shown):
    """--from names a model format, so it keeps meaning what it meant before results were
    detected: read the mesh alone."""
    from ada.api import cli as cli_module

    loaded = []

    class _Model:
        def show(self, **kw):
            loaded.append(kw)

    monkeypatch.setattr(cli_module, "_load", lambda *a, **kw: _Model())

    assert ada_cli.main.main(["view", str(example_files / _RMED_SHELL), "--from", "code_aster"]) == 0
    assert len(loaded) == 1
    assert shown == []


@pytest.mark.parametrize(
    "argv,message",
    [
        (["--renderer", "trimesh"], "not trimesh"),
        (["--super-element", "1"], "applies to a Sesam .SIN"),
    ],
)
def test_view_refuses_what_a_result_file_cannot_do(example_files, shown, capsys, argv, message):
    rc = ada_cli.main.main(["view", str(example_files / _RMED_SHELL), *argv])

    assert rc == 2
    assert message in capsys.readouterr().err
    assert shown == []


@pytest.mark.parametrize("flag", [["--stats"], ["--super-element", "1"]])
def test_view_refuses_result_options_on_a_model(example_files, capsys, flag):
    rc = ada_cli.main.main(["view", str(example_files / "fem_files/abaqus/box.inp"), *flag])

    assert rc == 2
    assert "ada view: error:" in capsys.readouterr().err


# ── ada results ───────────────────────────────────────────────────────────


def test_results_stats_prints_the_overview(example_files, capsys):
    assert ada_cli.main.main(["results", "stats", str(example_files / _SIN)]) == 0

    out = capsys.readouterr().out
    assert out.startswith("FEAResult(name='STATIC_SHELL_CANTILEVER_SESAMR1'")
    assert "Fields (" in out


def test_results_stats_refuses_a_model(example_files, capsys):
    assert ada_cli.main.main(["results", "stats", str(example_files / "fem_files/abaqus/box.inp")]) == 2
    assert "is not a result file" in capsys.readouterr().err


def test_results_compare_prints_then_shows_the_difference(example_files, shown, capsys):
    sin = str(example_files / _SIN)

    assert ada_cli.main.main(["results", "compare", sin, sin, "--renderer", "pygfx"]) == 0

    out = capsys.readouterr().out
    assert "differing=0" in out
    assert "identical:" in out
    assert len(shown) == 1
    name, kw = shown[0]
    assert name == "STATIC_SHELL_CANTILEVER_SESAMR1 - STATIC_SHELL_CANTILEVER_SESAMR1"
    assert kw == {"renderer": "pygfx", "host": "localhost", "port": 8765}


def test_results_compare_no_show(example_files, shown):
    sin = str(example_files / _SIN)

    assert ada_cli.main.main(["results", "compare", sin, sin, "--no-show"]) == 0
    assert shown == []


def _count_reads(monkeypatch) -> dict[str, list]:
    """The files each reader opened: ``chunks`` read a chunk at a time, ``full`` whole."""
    from ada.api import cli as cli_module

    reads = {"chunks": [], "full": []}
    for attr, kind in (("_open_result_chunks", "chunks"), ("_load_results", "full")):
        real = getattr(cli_module, attr)

        def _read(path, super_element=None, real=real, kind=kind):
            out = real(path, super_element)
            if out is not None:
                reads[kind].append(path)
            return out

        monkeypatch.setattr(cli_module, attr, _read)
    return reads


def test_results_compare_reads_one_file_given_twice_once(example_files, shown, monkeypatch, capsys):
    reads = _count_reads(monkeypatch)
    sin = str(example_files / _SIN)

    assert ada_cli.main.main(["results", "compare", sin, sin, "--no-show"]) == 0
    assert reads == {"chunks": [sin], "full": []}
    assert "differing=0" in capsys.readouterr().out


def test_results_compare_streams_two_sin_files(example_files, shown, monkeypatch, capsys, tmp_path):
    import shutil

    reads = _count_reads(monkeypatch)
    sin = example_files / _SIN
    copy = tmp_path / sin.name
    shutil.copyfile(sin, copy)

    assert ada_cli.main.main(["results", "compare", str(sin), str(copy), "--no-show"]) == 0
    assert reads == {"chunks": [str(sin), str(copy)], "full": []}
    out = capsys.readouterr().out
    assert "differing=0" in out
    assert "identical:" in out


def test_results_compare_of_other_formats_reads_them_whole(example_files, shown, monkeypatch, capsys):
    reads = _count_reads(monkeypatch)
    sif = str(example_files / _SIN.replace(".SIN", ".SIF"))

    assert ada_cli.main.main(["results", "compare", sif, sif, "--no-show"]) == 0
    assert reads == {"chunks": [], "full": [sif]}
    assert "differing=0" in capsys.readouterr().out


def test_results_compare_on_different_meshes_is_a_usage_error(example_files, shown, capsys):
    """The wrong pair of files is the caller's mistake, and the message names the CLI's flag
    rather than the Python keyword."""
    argv = ["results", "compare", str(example_files / _RMED_SHELL), str(example_files / _RMED_LINE)]

    assert ada_cli.main.main(argv) == 2
    err = capsys.readouterr().err
    assert "the meshes differ" in err
    assert "--no-check-mesh" in err
    assert shown == []


def test_super_element_is_repeatable():
    parse = ada_cli.main._build_parser().parse_args
    args = parse(["results", "stats", "a.SIN", "--super-element", "SEL10.IND1", "--super-element", "12"])
    assert args.super_element == ["SEL10.IND1", "12"]

    from ada.api.cli import _super_element_arg

    assert _super_element_arg(None) is None
    assert _super_element_arg(["12"]) == "12"
    assert _super_element_arg(["10", "12"]) == ["10", "12"]
