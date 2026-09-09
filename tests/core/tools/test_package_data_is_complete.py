"""Data files that live beside python modules must be named in `package-data`.

THE FAILURE THIS PREVENTS IS SILENT AT EVERY STAGE. A data file omitted from
`[tool.setuptools.package-data]` is still on disk in a checkout, so every test
passes; the build emits no warning, because nothing distinguishes a file you forgot
from a file you meant to leave out; and the fault appears only when something reads
it from an installed package.

It happened: `ada/comms/rest/migrations/*.sql` was not listed, so the built package
carried 30 migrations in the repository and 0 in the wheel. The REST API applies
those at boot, so a conda- or wheel-installed deployment could not create its own
schema — while a deployment running from a checkout worked perfectly.

This is the cheap half of the guard, and it runs everywhere. The expensive half is
building the conda recipe against the checkout and looking inside the artifact
(see tools/localise_feedstock_recipe.py); that catches the same class and more, but
needs a build.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]
_PKG = _REPO / "src" / "ada"

pytestmark = pytest.mark.skipif(
    not (_REPO / "pyproject.toml").is_file(),
    reason="reads the repository's pyproject.toml; a packaged test run has no checkout",
)


def _declared_patterns() -> list[str]:
    text = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"\[tool\.setuptools\.package-data\]\s*\nada\s*=\s*\[(.*?)\]", text, re.S)
    assert block, "could not find [tool.setuptools.package-data] ada = [...]"
    return re.findall(r'"([^"]+)"', block.group(1))


def _is_declared(relative: pathlib.PurePosixPath, patterns: list[str]) -> bool:
    return any(relative.match(pattern) for pattern in patterns)


#: Suffixes that are data rather than code. Deliberately a short list of the kinds
#: this package actually ships: broadening it to "anything not .py" would sweep in
#: caches and editor droppings and make the test a nuisance rather than a guard.
_DATA_SUFFIXES = {".sql", ".json", ".xml", ".zip"}

#: Directories whose contents are inputs to the build rather than package data.
_NOT_PACKAGE_DATA = {"__pycache__"}


def test_every_data_file_under_src_ada_is_declared():
    """Nothing shipped-looking is left out of `package-data`.

    Failing here means the file is on disk for you and absent for everyone who
    installs the package.
    """
    missing: list[str] = []
    patterns = _declared_patterns()
    for path in _PKG.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _DATA_SUFFIXES:
            continue
        if any(part in _NOT_PACKAGE_DATA for part in path.parts):
            continue
        relative = pathlib.PurePosixPath(path.relative_to(_PKG).as_posix())
        if not _is_declared(relative, patterns):
            missing.append(str(relative))

    assert not missing, (
        "these data files are not covered by [tool.setuptools.package-data] in pyproject.toml, "
        "so they exist in a checkout and are absent from an installed package:\n  " + "\n  ".join(sorted(missing))
    )


def test_the_rest_migrations_are_declared_by_name():
    """Named on its own because it is the one that already went wrong, and because
    the consequence is severe and remote: the API applies these at boot, so a
    package missing them cannot create its schema, and the error surfaces on a
    deployment rather than in a build."""
    patterns = _declared_patterns()
    migrations = sorted((_PKG / "comms" / "rest" / "migrations").glob("*.sql"))
    assert migrations, "no migrations found; has the directory moved?"
    for path in migrations:
        relative = pathlib.PurePosixPath(path.relative_to(_PKG).as_posix())
        assert _is_declared(relative, patterns), f"{relative} would not ship"


def test_a_declared_pattern_that_matches_nothing_is_reported():
    """A pattern for a file that no longer exists is dead weight that reads as
    coverage. Not fatal — a glob may legitimately be empty in a partial checkout —
    so it is a warning rather than a failure."""
    patterns = _declared_patterns()
    unmatched = [p for p in patterns if not list(_PKG.glob(p))]
    if unmatched:
        pytest.skip(f"package-data patterns matching nothing right now: {unmatched}")
