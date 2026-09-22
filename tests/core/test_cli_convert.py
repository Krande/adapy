"""What ``ada convert`` does, pinned at the implementation rather than the parser.

Every test calls :func:`ada.api.cli._cmd_convert` with a hand-built ``argparse.Namespace``,
so nothing here depends on ``ada_cli.main``'s parser (covered by ``test_cli_main.py``) and a
failure points at the conversion engine.

The two defects these regressions exist for:

* ``ada convert src.inp out.FEM`` used to die with ``Unsupported output file format: 'fem'``
  -- four of adapy's five FEM writers were unreachable from the CLI.
* ``ada convert src.inp out.inp`` used to "succeed" by creating a *directory* ``out.inp/``
  holding ``Ada/Ada.inp``, because the output path was passed as ``to_fem``'s ``scratch_dir``
  and the deck was named after the model. No FEM conversion produced the file the user named.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import pathlib

import pytest

import ada
from ada.api.cli import (
    _cmd_convert,
    _load,
    _resolve_read_format,
    _resolve_write_format,
    _write_fem,
)
from ada_cli import CliUsageError
from ada_cli.formats import FEM_WRITE_PRIMARY


def _ns(input_file, output_file, to_format=None, from_format=None) -> argparse.Namespace:
    """The namespace ``ada_cli.main``'s convert subparser hands the implementation."""
    return argparse.Namespace(
        input=str(input_file),
        output=str(output_file),
        from_format=from_format,
        to_format=to_format,
        split=False,
        limit=None,
    )


@contextlib.contextmanager
def _ada_logs(level: int):
    """Collect ``ada`` log records.

    Not ``caplog``: ``ada.config.configure_logger`` sets ``propagate = False`` on the ``ada``
    logger, so records never reach the root handler caplog installs. Attaching a handler
    directly is independent of whether that configuration has run.
    """
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collect(level=level)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(min(previous, level) if previous else level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.fixture(scope="module")
def src_inp(tmp_path_factory) -> pathlib.Path:
    """An Abaqus deck every one of the five FEM writers can re-emit.

    Built rather than taken from ``files/``: the shell beam's elements all carry a
    ``FemSection``, which the Calculix element writer requires (``files/fem_files/abaqus/
    box.inp`` has unsectioned solids and trips a separate, pre-existing Calculix bug).
    """
    tmp = tmp_path_factory.mktemp("cli_convert_src")
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("MyAssembly") / (ada.Part("MyPart", fem=bm.to_fem_obj(0.1, "shell")) / bm)
    a.to_fem("src", fem_format="abaqus", scratch_dir=tmp, overwrite=True, write_input_files_only=True)
    deck = tmp / "src" / "src.inp"
    assert deck.is_file()
    return deck


def _leftovers(directory: pathlib.Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


# --------------------------------------------------------------------------------------
# Gap 2 -- OUT is the file the user named
# --------------------------------------------------------------------------------------


def test_abaqus_output_is_the_named_file_not_a_directory(example_files, tmp_path):
    """The Gap-2 regression, on the real example deck the defect was measured with.

    ``out.inp`` must be a file. Before the fix it was a directory containing ``Ada/Ada.inp``.
    """
    out = tmp_path / "box_out.inp"
    _cmd_convert(_ns(example_files / "fem_files/abaqus/box.inp", out))

    assert out.is_file()
    assert not out.is_dir()
    assert out.stat().st_size > 0
    assert "*Heading" in out.read_text(errors="replace")
    # No model-named scratch folder and no leftover temp directory.
    assert _leftovers(tmp_path) == ["box_out.inp"]


def test_no_temp_directory_survives_a_successful_convert(src_inp, tmp_path):
    out = tmp_path / "deck.inp"
    _cmd_convert(_ns(src_inp, out))

    assert out.is_file()
    assert not list(tmp_path.glob(".ada-convert-*"))


def test_output_parent_directory_is_created(src_inp, tmp_path):
    out = tmp_path / "nested" / "deck.FEM"
    _cmd_convert(_ns(src_inp, out))

    assert out.is_file()


# --------------------------------------------------------------------------------------
# Gap 1 -- every FEM writer is reachable, and lands on OUT
# --------------------------------------------------------------------------------------


def test_sesam_fem_is_reachable_with_no_flags(src_inp, tmp_path):
    """The motivating command: ``ada convert src.inp out.FEM``.

    It used to raise ``Unsupported output file format: 'fem'``. The Sesam writer emits
    ``<name>T1.FEM``; the deck must nevertheless end up at the path the user typed.
    """
    out = tmp_path / "model.FEM"
    _cmd_convert(_ns(src_inp, out))

    assert out.is_file()
    assert out.read_text(errors="replace").startswith("IDENT")
    assert _leftovers(tmp_path) == ["model.FEM"]


def test_sesam_t1_suffix_is_not_doubled(src_inp, tmp_path):
    """Naming the target the way Sesam itself would must not produce ``modelT1T1.FEM``."""
    out = tmp_path / "modelT1.FEM"
    _cmd_convert(_ns(src_inp, out, to_format="sesam"))

    assert out.is_file()
    assert not (tmp_path / "modelT1T1.FEM").exists()
    assert _leftovers(tmp_path) == ["modelT1.FEM"]


def test_sesam_sidecar_sestra_inp_lands_beside_the_deck(tmp_path):
    """A model with an analysis step gets a ``sestra.inp``; it belongs next to OUT, not inside
    a scratch folder, and its ``INAM`` prefix must match the deck's ``T1`` stem."""
    from ada.fem import StepImplicitStatic

    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("WithStep") / (ada.Part("P", fem=bm.to_fem_obj(0.1, "shell")) / bm)
    a.fem.add_step(StepImplicitStatic("static", total_time=1, max_incr=1, init_incr=1))

    out = tmp_path / "runT1.FEM"
    written = _write_fem(a, out, "sesam")

    assert written[0] == out.resolve()
    assert out.is_file()
    sestra = tmp_path / "sestra.inp"
    assert sestra in [pathlib.Path(p) for p in written]
    assert sestra.is_file()
    # T1 stripped, so the Sesam prefix in sestra.inp agrees with runT1.FEM on disk.
    assert "INAM  run\n" in sestra.read_text(errors="replace")
    assert not list(tmp_path.glob(".ada-convert-*"))


def test_usfos_is_reachable_only_through_an_explicit_to_flag(src_inp, tmp_path):
    """USFOS ignores the name it is handed and always writes ``ufo_bulk.fem``; OUT still wins."""
    out = tmp_path / "jacket.fem"
    _cmd_convert(_ns(src_inp, out, to_format="usfos"))

    assert out.is_file()
    assert not (tmp_path / "ufo_bulk.fem").exists()
    assert out.read_text(errors="replace").lstrip().startswith("HEAD")
    assert _leftovers(tmp_path) == ["jacket.fem"]


def test_calculix_is_reachable_only_through_an_explicit_to_flag(src_inp, tmp_path):
    """``.inp`` defaults to Abaqus, so Calculix needs ``--to``. Also covers the zero-steps
    guard end to end: a converted model carries no analysis step."""
    out = tmp_path / "ccx.inp"
    _cmd_convert(_ns(src_inp, out, to_format="calculix"))

    assert out.is_file()
    text = out.read_text(errors="replace")
    assert "(Calculix)" in text
    assert "** No steps" in text
    assert _leftovers(tmp_path) == ["ccx.inp"]


def test_code_aster_writes_the_med_plus_its_sidecars(src_inp, tmp_path, capsys):
    """Code_Aster is genuinely multi-file: the ``.med`` is OUT, the rest land beside it and
    every path written is printed, primary first."""
    out = tmp_path / "study.med"
    _cmd_convert(_ns(src_inp, out))

    assert out.is_file()
    assert (tmp_path / "study.comm").is_file()
    assert (tmp_path / "study.name_map.json").is_file()
    assert (tmp_path / "study.adapy_fem.json").is_file()
    assert not list(tmp_path.glob(".ada-convert-*"))

    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert printed[0] == str(out.resolve())
    assert set(printed[1:]) == {
        str((tmp_path / "study.comm").resolve()),
        str((tmp_path / "study.name_map.json").resolve()),
        str((tmp_path / "study.adapy_fem.json").resolve()),
    }


# --------------------------------------------------------------------------------------
# Format resolution (Q2)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [("a.inp", "abaqus"), ("a.fem", "sesam"), ("a.FEM", "sesam"), ("a.sif", "sesam"), ("a.med", "code_aster")],
)
def test_read_format_defaults_match_the_library(name, expected):
    """The tie-break for a shared extension is the one ``interpret_fem_format_from_path``
    already makes, so ``ada convert`` and ``ada.from_fem`` never disagree."""
    assert _resolve_read_format(name, None) == expected


@pytest.mark.parametrize(
    "name, expected",
    [("a.inp", "abaqus"), ("a.fem", "sesam"), ("a.FEM", "sesam"), ("a.med", "code_aster"), ("a.glb", "gltf")],
)
def test_write_format_defaults(name, expected):
    assert _resolve_write_format(name, None) == expected


def test_an_explicit_flag_always_beats_the_extension():
    assert _resolve_write_format("a.inp", "calculix") == "calculix"
    assert _resolve_write_format("a.fem", "usfos") == "usfos"
    assert _resolve_read_format("a.inp", "sesam") == "sesam"


def test_a_shared_extension_says_which_format_it_picked():
    with _ada_logs(logging.INFO) as records:
        assert _resolve_write_format("a.fem", None) == "sesam"
    messages = [r.getMessage() for r in records]
    assert any("inferred from '.fem'" in m and "--to usfos" in m for m in messages)


def test_an_unusual_extension_warns_but_is_honoured(src_inp, tmp_path):
    """``--to`` wins over the extension, loudly. The deck still lands where it was asked for."""
    out = tmp_path / "odd.fem"
    with _ada_logs(logging.WARNING) as records:
        _cmd_convert(_ns(src_inp, out, to_format="abaqus"))

    assert out.is_file()
    assert "*Heading" in out.read_text(errors="replace")
    assert any("the usual extension is .inp" in r.getMessage() for r in records)


# --------------------------------------------------------------------------------------
# Usage errors (exit 2 via CliUsageError), all before the input is parsed
# --------------------------------------------------------------------------------------


def test_missing_input_is_a_usage_error(tmp_path):
    with pytest.raises(CliUsageError, match="input file not found"):
        _cmd_convert(_ns(tmp_path / "nope.inp", tmp_path / "out.inp"))


def test_unknown_output_extension_is_a_usage_error(src_inp, tmp_path):
    with pytest.raises(CliUsageError, match=r"cannot infer the output format from '\.xyz'"):
        _cmd_convert(_ns(src_inp, tmp_path / "out.xyz"))


def test_unknown_input_extension_is_a_usage_error(tmp_path):
    src = tmp_path / "deck.dat"
    src.write_text("")
    with pytest.raises(CliUsageError, match=r"cannot infer the input format from '\.dat'"):
        _cmd_convert(_ns(src, tmp_path / "out.inp"))


def test_an_extensionless_path_is_a_usage_error_that_says_so(src_inp, tmp_path):
    with pytest.raises(CliUsageError, match="no extension"):
        _cmd_convert(_ns(src_inp, tmp_path / "out"))


def test_code_aster_output_must_be_the_med(src_inp, tmp_path):
    with pytest.raises(CliUsageError, match=r"name the output \*\.med"):
        _cmd_convert(_ns(src_inp, tmp_path / "study.comm", to_format="code_aster"))


def test_naming_a_comm_without_a_to_flag_points_at_the_med(src_inp, tmp_path):
    with pytest.raises(CliUsageError, match=r"name the output \*\.med"):
        _cmd_convert(_ns(src_inp, tmp_path / "study.comm"))


def test_an_existing_directory_as_out_is_a_usage_error(src_inp, tmp_path):
    target = tmp_path / "out.inp"
    target.mkdir()
    with pytest.raises(CliUsageError, match="OUT must name a file, not a directory"):
        _cmd_convert(_ns(src_inp, target))


def test_a_trailing_separator_on_out_is_a_usage_error(src_inp, tmp_path):
    """A path the user meant as a directory is refused even when it carries an extension --
    which is exactly the shape of the old ``out.inp/`` directory this CLI used to create."""
    with pytest.raises(CliUsageError, match="OUT must name a file, not a directory"):
        _cmd_convert(_ns(src_inp, f"{tmp_path / 'out.inp'}/"))


def test_every_usage_error_is_raised_before_the_input_is_read(src_inp, tmp_path, monkeypatch):
    """A usage error must not cost a multi-minute parse of a large deck first.

    The directory-shaped ``OUT`` case matters most: its extension resolves fine, so nothing
    but an explicit up-front check stops the read from happening first.
    """
    from ada.api import cli

    def _boom(*args, **kwargs):
        raise AssertionError("the input was read before the invocation was validated")

    monkeypatch.setattr(cli, "_load", _boom)

    existing_dir = tmp_path / "taken.inp"
    existing_dir.mkdir()

    for namespace in (
        _ns(src_inp, tmp_path / "out.xyz"),
        _ns(src_inp, tmp_path / "out.inp", from_format="nonsense"),
        _ns(src_inp, tmp_path / "out.comm"),
        _ns(src_inp, existing_dir),
        _ns(src_inp, f"{tmp_path / 'trailing.inp'}/"),
    ):
        with pytest.raises(CliUsageError):
            _cmd_convert(namespace)


# --------------------------------------------------------------------------------------
# Failure handling inside _write_fem
# --------------------------------------------------------------------------------------


class _FakeModel:
    """Stands in for an Assembly so a writer failure can be provoked without a real model."""

    def __init__(self, on_write=None):
        self.on_write = on_write
        self.calls: list[pathlib.Path] = []

    def to_fem(self, name, fem_format, scratch_dir, overwrite, write_input_files_only):
        self.calls.append(pathlib.Path(scratch_dir))
        if self.on_write is not None:
            self.on_write(name, pathlib.Path(scratch_dir))


def test_the_temp_directory_is_removed_when_the_writer_raises(tmp_path):
    def _explode(name, scratch_dir):
        (scratch_dir / name).mkdir(parents=True, exist_ok=True)
        (scratch_dir / name / "half-written.inp").write_text("partial")
        raise ValueError("writer blew up")

    model = _FakeModel(_explode)
    with pytest.raises(ValueError, match="writer blew up"):
        _write_fem(model, tmp_path / "out.inp", "abaqus")

    assert not list(tmp_path.glob(".ada-convert-*"))
    assert _leftovers(tmp_path) == []
    # The temp dir really was next to OUT, so the final move would have been a rename.
    assert model.calls[0].parent == tmp_path.resolve()


def test_a_missing_primary_file_is_a_writer_bug_not_a_usage_error(tmp_path):
    """A silent writer is a broken contract against ``FEM_WRITE_PRIMARY``: a ``RuntimeError``
    quoting what it did write, not a ``CliUsageError`` blaming the caller."""
    model = _FakeModel(lambda name, scratch: (scratch / name).mkdir(parents=True, exist_ok=True))

    with pytest.raises(RuntimeError) as excinfo:
        _write_fem(model, tmp_path / "out.inp", "abaqus")

    message = str(excinfo.value)
    assert "FEM_WRITE_PRIMARY" in message
    assert FEM_WRITE_PRIMARY["abaqus"].format(name="out") in message
    assert not isinstance(excinfo.value, CliUsageError)
    assert not list(tmp_path.glob(".ada-convert-*"))


# --------------------------------------------------------------------------------------
# The loader ada view shares
# --------------------------------------------------------------------------------------


def test_the_shared_loader_still_reads_a_deck(src_inp):
    model = _load(src_inp)
    assert isinstance(model, ada.Assembly)
    assert len(model.fem.nodes) > 0 or any(len(p.fem.nodes) > 0 for p in model.get_all_subparts())


def test_the_shared_loader_takes_an_explicit_format(example_files, tmp_path):
    """``--from`` reads a deck whose extension says nothing -- the case the flag exists for.

    Reading ``contact2e.inp`` with ``fmt="abaqus"`` would prove nothing, because inference
    picks abaqus for ``.inp`` anyway: that version passed with the ``fmt`` plumbing removed.
    Copying the deck to ``.dat`` first makes the flag the only thing that can work.
    """
    deck = tmp_path / "deck.dat"
    deck.write_bytes((example_files / "fem_files/calculix/contact2e.inp").read_bytes())

    with pytest.raises(CliUsageError, match="--from"):
        _load(deck)

    model = _load(deck, fmt="abaqus")
    assert isinstance(model, ada.Assembly)


def test_the_shared_loader_reports_a_missing_file_as_a_usage_error(tmp_path):
    with pytest.raises(CliUsageError, match="input file not found"):
        _load(tmp_path / "gone.ifc")


@pytest.mark.parametrize(
    "out_name, to_format",
    [
        ("model.v2.inp", None),
        ("model.v2.FEM", None),
        ("model.v2.fem", "usfos"),
        ("ccx.v2.inp", "calculix"),
        ("study.2024.med", None),
        ("beam_0.5m.FEM", None),
    ],
)
def test_a_dot_in_the_output_name_still_lands_where_it_was_named(src_inp, tmp_path, out_name, to_format):
    """``model.v2.FEM`` and ``beam_0.5m.FEM`` are ordinary names, and must work.

    The sesam, calculix and code_aster writers build their filenames with
    ``Path.with_suffix``, which *replaces* an existing suffix -- so handing them the stem
    ``model.v2`` made sesam emit ``model.FEM``, the primary file was not where
    ``FEM_WRITE_PRIMARY`` said, and the conversion died with a RuntimeError blaming the
    writer. Three of the five FEM formats failed on a name a user would reasonably pick.
    """
    out = tmp_path / "out" / out_name
    _cmd_convert(_ns(src_inp, out, to_format=to_format))

    assert out.is_file(), f"{out_name} was not delivered"
    assert out.stat().st_size > 0
    assert not list(tmp_path.glob("out/.ada-convert-*")), "temp directory left behind"
