"""Everything that claims to describe the ``ada`` CLI must agree with the parser.

Four places state the CLI's surface, and none of them are generated: the module docstring in
``ada_cli/main.py``, the README table, the docs page, and — out of tree, so only the entry-point
name can be pinned from here — the conda recipe. They drift the moment a subcommand is added, and
the drift is invisible: the code keeps working, the prose just quietly stops being true. It already
had: ``ada audit`` was absent from the ``main.py`` listing that purports to show the subcommand
layout, and ``audit``'s own listing was missing ``profile``, ``logfile`` and ``parity``.

So these tests derive the surface from ``_build_parser()`` and compare, in both directions — a
command the docs invent is as wrong as one they omit.

The other trap is the name. The distribution is ``ada-py``; the console script is ``ada``. "Install
ada-py, then run ada-py" is a natural sentence to write and it does not work, so no surface is
allowed to say it.
"""

from __future__ import annotations

import argparse
import pathlib
import re

import pytest

from ada_cli.main import _build_parser

_REPO = pathlib.Path(__file__).parents[2]
_PYPROJECT = _REPO / "pyproject.toml"
_README = _REPO / "README.md"
_DOCS_PAGE = _REPO / "docs" / "documents" / "cli.rst"

_DISTRIBUTION = "ada-py"
_COMMAND = "ada"

# Only the repo tree carries the prose surfaces; the wheel/sdist test env has tests/ and files/ and
# nothing else, so skip there rather than fail.
_needs_repo = pytest.mark.skipif(
    not _PYPROJECT.is_file(),
    reason=f"no repo tree at {_REPO} (sdist/wheel test env) — docs/ and README.md are not packaged",
)

# A line that is nothing but a command literal: an rst section title or a definition-list term.
# Code blocks in the page are indented, so examples never match.
_RST_COMMAND_LINE = re.compile(r"^``(ada(?: [a-z0-9][a-z0-9-]*)+)``$", re.MULTILINE)
# Inline code holding a command, e.g. the README table's first column.
_MD_COMMAND = re.compile(r"`(ada [a-z0-9][a-z0-9-]*)`")
_MD_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
# The subcommand-layout block in ada_cli/main.py's docstring.
_DOCSTRING_COMMAND = re.compile(r"^ {4}ada ([a-z0-9][a-z0-9-]*)", re.MULTILINE)


def _command_paths(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> set[str]:
    """Every invocable command path below ``parser``, space-joined and without the ``ada`` prog."""
    found: set[str] = set()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            path = prefix + (name,)
            found.add(" ".join(path))
            found |= _command_paths(sub, path)
    return found


def _real_commands() -> set[str]:
    return _command_paths(_build_parser())


def _real_top_level() -> set[str]:
    return {c for c in _real_commands() if " " not in c}


def _diff(documented: set[str], real: set[str], surface: str) -> str:
    return (
        f"{surface} has drifted from ada_cli.main._build_parser():\n"
        f"  documented but not a real command: {sorted(documented - real) or 'none'}\n"
        f"  real command but undocumented:     {sorted(real - documented) or 'none'}"
    )


# ── the name ──────────────────────────────────────────────────────────────


@_needs_repo
def test_the_console_script_is_ada_not_the_distribution_name():
    """One entry point, named ``ada``. The conda recipe restates this by hand out of tree, so a
    rename here silently leaves the conda package installing the old name."""
    scripts = re.search(r"^\[project\.scripts\]\n((?:.+\n)*?)(?:\n|\[)", _PYPROJECT.read_text(encoding="utf-8"), re.M)
    assert scripts, "no [project.scripts] table in pyproject.toml"
    entries = dict(
        (k.strip(), v.strip().strip('"'))
        for k, _, v in (line.partition("=") for line in scripts.group(1).splitlines() if line.strip())
    )
    assert entries == {_COMMAND: "ada_cli.main:main"}, f"unexpected console scripts: {entries}"
    assert _build_parser().prog == _COMMAND


@_needs_repo
def test_no_surface_tells_you_to_run_the_distribution_name():
    """``ada-py convert ...`` is the natural thing to write and it is not a command."""
    verbs = "|".join(sorted(_real_top_level() | {"--help"}))
    misuse = re.compile(rf"\b{re.escape(_DISTRIBUTION)}\s+(?:{verbs})\b")
    offenders = []
    for path in (_README, _DOCS_PAGE, _REPO / "src" / "ada_cli" / "main.py"):
        # Strip the markup that would otherwise sit between the two tokens.
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if misuse.search(line.replace("`", "")):
                offenders.append(f"{path.relative_to(_REPO)}:{i}: {line.strip()}")
    assert not offenders, f"the distribution is {_DISTRIBUTION!r} but the command is {_COMMAND!r}:\n  " + "\n  ".join(
        offenders
    )


# ── the surface ───────────────────────────────────────────────────────────


@_needs_repo
def test_docs_page_documents_every_command():
    """The docs page is the full reference, so it must cover nested subcommands too."""
    documented = set(_RST_COMMAND_LINE.findall(_DOCS_PAGE.read_text(encoding="utf-8")))
    documented = {c[len(_COMMAND) + 1 :] for c in documented}
    real = _real_commands()
    assert documented == real, _diff(documented, real, "docs/documents/cli.rst")


@_needs_repo
def test_readme_lists_every_top_level_command():
    """The README summarises; it owes the reader every group, not every flag."""
    body = _MD_FENCE.sub("", _README.read_text(encoding="utf-8"))
    documented = {c.split(" ", 1)[1] for c in _MD_COMMAND.findall(body)}
    real = _real_top_level()
    assert documented == real, _diff(documented, real, "README.md")


def test_cli_module_docstring_lists_every_top_level_command():
    """The docstring calls itself the "subcommand layout", and it was missing one."""
    import ada_cli.main

    documented = set(_DOCSTRING_COMMAND.findall(ada_cli.main.__doc__ or ""))
    real = _real_top_level()
    assert documented == real, _diff(documented, real, "the ada_cli.main module docstring")
