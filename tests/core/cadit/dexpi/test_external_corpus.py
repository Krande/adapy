"""The wider DEXPI corpus, run only when it has been fetched locally.

``scripts/fetch_dexpi_testcases.py`` downloads the official DEXPI TrainingTestCases (CC BY 4.0)
into ``files/dexpi_files/_external/``, a git-ignored directory the repository never ships. This
suite parses every ``*.xml`` under it and asserts T1 -- ``file -> doc -> XML -> doc'`` compares
equal under :func:`~ada.cadit.dexpi.canonical.canonicalize` -- the same oracle
``test_write_proteus.py``/``test_write_dexpi20.py`` hold the checked-in fixtures and the two
vendored official files to, just over a much larger, unvendored set of real emitter output.

**No network access happens here.** The module makes zero requests; it only looks at a directory
that may or may not already exist on disk. When it does not,
``pytestmark`` skips the whole module with a message naming the fetch script, so
``pixi run -e tests test-core`` (and CI, which never runs the fetch script) stays hermetic. The
parametrization always yields at least one collected test item -- a ``None`` placeholder when the
corpus is absent -- specifically so the skip is visible in a test run rather than the module
silently vanishing from collection.
"""

from __future__ import annotations

import pathlib

import pytest

from ada.cadit.dexpi import canonicalize, read_dexpi, validate_document
from ada.cadit.dexpi.write import write_dexpi

_REPO = pathlib.Path(__file__).resolve().parents[4]
_EXT = _REPO / "files" / "dexpi_files" / "_external"

pytestmark = pytest.mark.skipif(not _EXT.is_dir(), reason="run scripts/fetch_dexpi_testcases.py first")

_EXT_FILES: list[pathlib.Path] = sorted(_EXT.rglob("*.xml")) if _EXT.is_dir() else []


def _id(path: pathlib.Path | None) -> str:
    return path.relative_to(_EXT).as_posix() if path is not None else "no-corpus"


@pytest.mark.parametrize("path", _EXT_FILES or [None], ids=_id)
def test_t1_external_corpus_file_survives_a_write_and_a_re_read(path, tmp_path):
    """``file -> doc -> XML -> doc'`` is canonically equal, for one file of the fetched corpus.

    Also runs :func:`~ada.cadit.dexpi.validate_document` and surfaces any structural findings as a
    warning rather than a failure: a few corpus files are known to carry constructs outside the
    curated class table (see the PR 3 note on the five DEXPI 1.3 plant-structure classes), which is
    harmless for the 3D path and not what this test exists to police. T1 is the hard assertion.
    """
    if path is None:
        pytest.fail(f"{_EXT} exists but contains no .xml files -- re-run scripts/fetch_dexpi_testcases.py")

    doc = read_dexpi(path)
    written = write_dexpi(doc, tmp_path / path.name)
    again = read_dexpi(written)

    problems = validate_document(again)
    if problems:
        import warnings

        warnings.warn(f"{path.relative_to(_EXT)}: validate_document found {problems}", stacklevel=1)

    assert canonicalize(again) == canonicalize(doc), f"T1 failed for {path.relative_to(_EXT)}"
