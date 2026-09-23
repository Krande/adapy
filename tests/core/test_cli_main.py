"""The ``ada`` front end: what a bare invocation does, what exits with what, and the one
import that must never happen.

``ada_cli`` exists for a single reason — ``ada --help`` builds its parser without importing
``ada``, so the CLI starts in milliseconds instead of paying for the whole CAD/FEM surface.
That is a property nothing in the code announces and every future edit can silently break
with one convenient top-level import, so :func:`test_parser_build_does_not_import_ada` checks
it in a subprocess. It has to be a subprocess: ``tests/conftest.py`` imports ``ada`` itself,
so in-process ``sys.modules`` can never answer the question.

The rest pins the invocation contract. Exit codes are the part scripts depend on and the part
that is easiest to get subtly wrong: a bare command prints *help* (an improvement over
argparse's one-line usage) but still exits 2 on stderr, because a wrong invocation must not
look like success to a caller reading stdout.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import ada_cli.main
from ada_cli import CliUsageError
from ada_cli.formats import (
    DEFAULT_READ_BY_EXT,
    DEFAULT_WRITE_BY_EXT,
    READ_FORMATS,
    WRITE_FORMATS,
    infer_read_format,
    infer_write_format,
)

_GROUPS = ["view", "build", "files", "audit", "serve"]


# ── bare invocations: full help, on stderr, exit 2 ────────────────────────


def test_bare_ada_prints_full_help_and_exits_2(capsys):
    """``ada`` alone used to answer with one line of usage. It now answers with the whole help,
    but on stderr and still non-zero — the improvement is the text, not the exit code."""
    rc = ada_cli.main.main([])

    assert rc == 2
    out, err = capsys.readouterr()
    assert out == "", f"help for a failed invocation must not reach stdout, got: {out[:200]!r}"
    assert err.startswith("usage: ada ")
    # "Full help", concretely: the per-command descriptions a one-line usage omits.
    assert "Convert between supported CAD/FEM formats" in err
    for group in _GROUPS:
        assert f"  {group}" in err, f"{group!r} missing from the top-level help"


def test_bare_ada_convert_prints_convert_help_and_exits_2(capsys):
    """The motivating case: someone types ``ada convert`` to find out how to use it."""
    rc = ada_cli.main.main(["convert"])

    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("usage: ada convert ")
    # The three things the reader came for.
    assert "--to FORMAT" in err
    assert "--from FORMAT" in err
    assert "--list-formats" in err


@pytest.mark.parametrize("group", _GROUPS)
def test_bare_group_commands_print_their_help(group, capsys):
    """Every command that cannot act on its own gets the same treatment, whether what it lacks
    is a nested subcommand (build/files/audit/serve) or a positional (view)."""
    rc = ada_cli.main.main([group])

    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"usage: ada {group} ")


def test_half_given_arguments_stay_an_argparse_error(capsys):
    """Only a *bare* command earns a screen of help. A specific mistake deserves a specific
    error, so ``ada convert IN`` must keep saying which argument is missing."""
    with pytest.raises(SystemExit) as exc:
        ada_cli.main.main(["convert", "in.inp"])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "the following arguments are required: output" in err
    assert "positional arguments:" not in err, "this is an error, not a help request"


# ── the explicit help/listing paths: stdout, exit 0 ───────────────────────


@pytest.mark.parametrize("argv", [["--help"], ["convert", "--help"], ["view", "--help"]])
def test_help_flags_exit_0_on_stdout(argv, capsys):
    """Asking for help is a success and belongs on stdout — the mirror image of the bare case."""
    with pytest.raises(SystemExit) as exc:
        ada_cli.main.main(argv)

    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert out.startswith("usage: ada")


def test_list_formats_exits_0_and_lists_every_format(capsys):
    """``--list-formats`` has to work *despite* convert's two required positionals — a user who
    needs the table does not yet have an input and an output to name. Hence the argparse Action
    with ``nargs=0``, the same mechanism ``--version`` uses."""
    with pytest.raises(SystemExit) as exc:
        ada_cli.main.main(["convert", "--list-formats"])

    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert err == ""

    for name in READ_FORMATS:
        assert name in out, f"read format {name!r} missing from --list-formats"
    for name in WRITE_FORMATS:
        assert name in out, f"write format {name!r} missing from --list-formats"
    for ext in {*DEFAULT_READ_BY_EXT, *DEFAULT_WRITE_BY_EXT}:
        assert f".{ext}" in out, f"extension .{ext} missing from --list-formats"
    # The two ambiguities are the reason the table exists at all.
    assert "--to calculix" in out
    assert "--to usfos" in out
    # Printed to a console, so it must survive one that is not UTF-8.
    out.encode("ascii")


# ── format flags ──────────────────────────────────────────────────────────


def test_invalid_to_choice_is_an_argparse_error(capsys):
    """``choices=`` comes straight from the table, so the list of valid formats in the error is
    the list of formats that exist. A typo must not reach the loader."""
    with pytest.raises(SystemExit) as exc:
        ada_cli.main.main(["convert", "in.inp", "out.fem", "--to", "nastran"])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'nastran'" in err
    for name in WRITE_FORMATS:
        assert name in err


def test_convert_namespace_attribute_names():
    """The namespace is the contract between this parser and ``ada.api.cli._cmd_convert``, which
    is in a different package and reads these six names. Renaming a ``dest`` here would break it
    at runtime and nowhere else, so pin the names and the ``None`` defaults."""
    args = ada_cli.main._build_parser().parse_args(["convert", "in.inp", "out.FEM"])

    assert args.input == "in.inp"
    assert args.output == "out.FEM"
    assert args.from_format is None
    assert args.to_format is None
    assert args.split is False
    assert args.limit is None

    args = ada_cli.main._build_parser().parse_args(
        ["convert", "in.dat", "out.fem", "--from", "abaqus", "--to", "usfos", "--split", "--limit", "5"]
    )
    assert (args.from_format, args.to_format, args.split, args.limit) == ("abaqus", "usfos", True, 5)

    # Short forms exist because both flags get typed constantly.
    args = ada_cli.main._build_parser().parse_args(["convert", "a.inp", "b.fem", "-f", "abaqus", "-t", "calculix"])
    assert (args.from_format, args.to_format) == ("abaqus", "calculix")


@pytest.mark.parametrize(
    "path,read_fmt,write_fmt",
    [
        # The motivating command's two halves, and the reason the defaults are what they are:
        # they match ada.fem.formats.general.interpret_fem_format_from_path, so `ada convert`
        # and `ada.from_fem` never disagree about the same filename.
        ("model.inp", "abaqus", "abaqus"),
        ("model.fem", "sesam", "sesam"),
        # Extensions are matched case-insensitively; Sesam decks are conventionally .FEM.
        ("model.FEM", "sesam", "sesam"),
        ("MODEL.INP", "abaqus", "abaqus"),
        ("model.sif", "sesam", None),
        ("model.med", "code_aster", "code_aster"),
        ("model.rmed", "code_aster", None),
        ("model.ifc", "ifc", "ifc"),
        ("model.stp", "step", "step"),
        ("model.step", "step", "step"),
        ("model.glb", None, "gltf"),
        ("model.sat", "acis", None),
        # No extension, and an extension nobody claims: inference declines rather than guessing.
        ("model.xyz", None, None),
        ("model", None, None),
        # A full path, not just a name.
        ("/tmp/some.dir/model.inp", "abaqus", "abaqus"),
    ],
)
def test_infer_defaults(path, read_fmt, write_fmt):
    """Extension inference is what makes ``ada convert a.inp b.FEM`` work with no flags, and
    ``None`` is what makes the "pass --to" error possible rather than a wrong guess."""
    assert infer_read_format(path) == read_fmt
    assert infer_write_format(path) == write_fmt


def test_minority_dialects_are_flag_only():
    """Calculix and USFOS share an extension with a format that owns it, so they are reachable
    only through ``--to``. If inference ever started returning them, a plain ``a.inp b.fem``
    would silently change format."""
    flag_only = {"calculix", "usfos"}
    inferable = set(DEFAULT_WRITE_BY_EXT.values())

    assert flag_only.isdisjoint(inferable)
    assert flag_only <= set(WRITE_FORMATS), "still offered by --to"


# ── errors from the implementation ────────────────────────────────────────


def test_cli_usage_error_from_impl_maps_to_exit_2(monkeypatch, capsys):
    """``ada.api.cli`` cannot reach the parser to call ``parser.error()``, so it raises
    ``CliUsageError`` and ``main`` renders it in argparse's own shape. A missing input file is a
    usage mistake, and it used to be a traceback."""

    def boom(args):
        raise CliUsageError("input file not found: nope.inp")

    monkeypatch.setattr(ada_cli.main, "_cmd_convert", boom)

    rc = ada_cli.main.main(["convert", "nope.inp", "out.FEM"])

    assert rc == 2
    out, err = capsys.readouterr()
    assert err.strip() == "ada convert: error: input file not found: nope.inp"
    assert out == ""


def test_non_usage_errors_still_propagate(monkeypatch):
    """The flip side: only usage mistakes become exit 2. A writer blowing up must keep its
    traceback, or a broken conversion would be indistinguishable from a typo."""

    def boom(args):
        raise RuntimeError("the writer produced no deck")

    monkeypatch.setattr(ada_cli.main, "_cmd_convert", boom)

    with pytest.raises(RuntimeError, match="produced no deck"):
        ada_cli.main.main(["convert", "in.inp", "out.FEM"])


# ── the startup-latency invariant ─────────────────────────────────────────


# Runs in a fresh interpreter because this test's own process has already imported `ada` (via
# tests/conftest.py), which would make any in-process sys.modules check vacuously pass.
_NO_ADA_PROBE = """
import contextlib, io, sys

def leaked():
    return sorted(n for n in sys.modules if n == "ada" or n.startswith("ada."))

import ada_cli.main as m
assert not leaked(), "importing ada_cli.main pulled in: %s" % leaked()

m._build_parser()
assert not leaked(), "_build_parser() pulled in: %s" % leaked()

with contextlib.redirect_stdout(io.StringIO()):
    try:
        m.main(["--help"])
    except SystemExit:
        pass
assert not leaked(), "main(['--help']) pulled in: %s" % leaked()

with contextlib.redirect_stdout(io.StringIO()):
    try:
        m.main(["convert", "--list-formats"])
    except SystemExit:
        pass
assert not leaked(), "main(['convert', '--list-formats']) pulled in: %s" % leaked()

with contextlib.redirect_stderr(io.StringIO()):
    assert m.main([]) == 2
    assert m.main(["convert"]) == 2
assert not leaked(), "a bare invocation pulled in: %s" % leaked()

print("OK")
"""


def test_parser_build_does_not_import_ada(tmp_path):
    """``ada_cli`` is a separate top-level package for one reason: the parser, the help and the
    format table must be reachable without importing ``ada``, whose ``__init__`` drags in the
    whole CAD/FEM surface. One convenient ``from ada...`` at module level anywhere in
    ``ada_cli`` (or in ``ada_cli.formats``, or in ``ada_cli.audit``, which ``_build_parser``
    calls) throws that away and nothing else would notice.

    Note what is covered: importing the module, building the parser, ``--help``,
    ``--list-formats`` and both bare invocations. Only actually *running* a conversion is
    allowed to import ``ada``, and it does so inside the command function.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    env.pop("PYTHONSTARTUP", None)

    proc = subprocess.run(
        [sys.executable, "-c", _NO_ADA_PROBE],
        capture_output=True,
        text=True,
        env=env,
        # A directory with no .env, so load_dotenv_cwd() has nothing to read and the probe is
        # not affected by where pytest was launched from.
        cwd=tmp_path,
    )

    assert proc.returncode == 0, (
        "ada_cli must build its parser without importing ada — that is the whole point of the "
        f"package living outside ada/.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.stdout.strip().endswith("OK")


# ── end to end, through main() ────────────────────────────────────────────


def test_end_to_end_inp_to_sesam_through_main(tmp_path, example_files, capsys):
    """The command the whole change exists for: ``ada convert model.inp model.FEM``.

    It crosses into the conversion engine (``ada.api.cli``), so it is the one test here that
    fails until that lands. What it pins is the user-visible promise rather than any internal:
    the output is a *file*, at exactly the path that was named — previously ``.inp`` output
    produced a directory holding a deck named after the model, and ``.FEM`` output was rejected
    outright — and the scratch directory used to get there is gone afterwards.
    """
    src = example_files / "fem_files" / "abaqus" / "box.inp"
    assert src.is_file(), f"test input missing: {src}"
    out = tmp_path / "box.FEM"

    rc = ada_cli.main.main(["convert", str(src), str(out)])

    assert rc == 0
    assert out.is_file(), f"expected a file at {out}, found {sorted(p.name for p in tmp_path.iterdir())}"
    assert out.stat().st_size > 0
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".ada-convert-")]
    assert not leftovers, f"scratch directories left behind: {leftovers}"
    # Every path written is reported, primary first.
    assert capsys.readouterr().out.splitlines()[0].strip() == str(out)


# ── --log-file ────────────────────────────────────────────────────────────


def _cli_env() -> dict[str, str]:
    """Environment for a subprocess ``ada`` run: this interpreter's import path, nothing else."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    env.pop("PYTHONSTARTUP", None)
    return env


def _run_convert(src, out, tmp_path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ada_cli.main", "convert", str(src), str(out), *extra],
        capture_output=True,
        text=True,
        env=_cli_env(),
        cwd=tmp_path,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["convert", "in.inp", "out.FEM", "--log-file", "run.log"],
        ["view", "in.inp", "--log-file", "run.log"],
    ],
    ids=["convert", "view"],
)
def test_log_file_is_accepted_after_the_positionals(argv):
    """It is a per-subcommand option, not a global one: argparse only takes the global ones
    before the subcommand, and this is the order it gets typed in."""
    args = ada_cli.main._build_parser().parse_args(argv)

    assert args.log_file == "run.log"


def test_log_file_takes_the_records_and_leaves_the_console_quiet(tmp_path, example_files):
    """The flag exists because of a 463k-element deck whose INFO stream was 1.18M lines: the
    records are worth keeping, just not on the terminal. A subprocess, because the console
    handler ``ada`` installs at import time holds the real ``sys.stderr`` and capsys never
    sees it."""
    src = example_files / "fem_files" / "abaqus" / "box.inp"
    out = tmp_path / "box.FEM"
    log = tmp_path / "convert.log"

    proc = _run_convert(src, out, tmp_path, "--log-file", str(log))

    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    text = log.read_text(encoding="utf-8")
    assert "INFO/ada" in text, f"the log file got no INFO records:\n{text[:500]}"
    assert "INFO" not in proc.stderr, f"INFO still reached the console:\n{proc.stderr[:500]}"


def test_without_log_file_the_console_still_gets_everything(tmp_path, example_files):
    """The other half of the promise: no flag, no change. Resolving the shared ``.fem``
    extension is logged at INFO, so there is always at least one record to see."""
    src = example_files / "fem_files" / "abaqus" / "box.inp"
    out = tmp_path / "box.FEM"

    proc = _run_convert(src, out, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "INFO/ada" in proc.stderr, f"INFO records stopped reaching the console:\n{proc.stderr[:500]}"
    assert not list(tmp_path.glob("*.log"))
