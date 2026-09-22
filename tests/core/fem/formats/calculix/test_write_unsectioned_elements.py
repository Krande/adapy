"""Calculix and elements that carry no FemSection.

A mesh-only deck -- an Abaqus ``.inp`` whose elements have no ``*SOLID SECTION``, which is
what ``files/fem_files/abaqus/box.inp`` is -- reads into elements with ``fem_sec is None``.
Calculix chooses its element type from the section, so there is nothing to write one from.

The writer used to dereference that ``None`` and fail with a bare
``AttributeError: 'NoneType' object has no attribute 'parent'`` from
``write_elements.el_type_sub``. That was unreachable from the CLI while ``--to calculix``
did not exist; exposing the format made a mesh-only model's first conversion an
uninterpretable traceback.

Now such elements are skipped and named, like the connectors/masses/springs the writer
already skipped -- and if that leaves nothing at all to write, it is an error rather than a
deck that reads like a successful conversion of an empty model.
"""

from __future__ import annotations

import contextlib
import logging
import re

import pytest

import ada
from ada.fem.exceptions import IncompatibleElements


@contextlib.contextmanager
def captured_warnings():
    """Records from adapy's own logger; ``configure_logger`` turns propagation off, so
    ``caplog`` never sees them."""
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collector(level=logging.WARNING)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _sectioned_shell_assembly() -> ada.Assembly:
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    return ada.Assembly("a") / (ada.Part("p", fem=pl.to_fem_obj(0.5, "shell")) / pl)


def test_a_mesh_only_model_is_refused_with_a_message_that_says_why(example_files, tmp_path):
    """The whole model is unsectioned: an error, not an empty deck."""
    a = ada.from_fem(example_files / "fem_files/abaqus/box.inp", fem_format="abaqus")
    assert all(el.fem_sec is None for el in next(iter(a.parts.values())).fem.elements), "fixture must be mesh-only"

    with pytest.raises(IncompatibleElements) as excinfo:
        a.to_fem("cx", fem_format="calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    message = str(excinfo.value)
    assert "500" in message, "says how many elements were lost"
    assert "FemSection" in message, "says what they were missing"
    # A partial file may remain: the writer streams the deck and the element block is what
    # raises, which is pre-existing behaviour for any writer error. What must not happen is
    # a deck carrying elements. The CLI never exposes even the partial file -- it writes
    # into a temporary directory and only moves the result into place on success.
    deck = tmp_path / "cx" / "cx.inp"
    if deck.exists():
        assert "*ELEMENT" not in deck.read_text()


def test_the_failure_is_not_a_bare_attributeerror(example_files, tmp_path):
    """Pins the regression itself: the old behaviour was an AttributeError on None."""
    a = ada.from_fem(example_files / "fem_files/abaqus/box.inp", fem_format="abaqus")
    with pytest.raises(Exception) as excinfo:
        a.to_fem("cx", fem_format="calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    assert not isinstance(excinfo.value, AttributeError)
    assert "NoneType" not in str(excinfo.value)


def test_unsectioned_elements_are_named_in_a_warning(example_files, tmp_path):
    a = ada.from_fem(example_files / "fem_files/abaqus/box.inp", fem_format="abaqus")
    with captured_warnings() as records:
        with pytest.raises(IncompatibleElements):
            a.to_fem("cx", fem_format="calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    text = " ".join(r.getMessage() for r in records)
    assert "no FemSection" in text and "500" in text


def test_a_sectioned_model_still_writes_its_elements(tmp_path):
    """The guard must not cost the normal path anything."""
    _sectioned_shell_assembly().to_fem(
        "cx", fem_format="calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True
    )
    deck = (tmp_path / "cx" / "cx.inp").read_text()
    assert "*ELEMENT" in deck and "*NODE" in deck


def test_a_partially_sectioned_model_writes_the_part_it_can(tmp_path):
    """Some sections present: a partial deck plus a warning, matching how the writer already
    treats element types it cannot represent."""
    a = _sectioned_shell_assembly()
    fem = next(iter(a.parts.values())).fem
    orphan = next(iter(fem.elements))
    orphan.fem_sec = None

    with captured_warnings() as records:
        a.to_fem("cx", fem_format="calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    deck = (tmp_path / "cx" / "cx.inp").read_text()
    assert "*ELEMENT" in deck, "the sectioned elements still make it"
    # Read the element ids out of the *ELEMENT block itself. Splitting the file on the
    # keyword is not enough: element id 1 also appears as a node id in *NODE.
    block = re.search(r"^\*ELEMENT[^\n]*\n((?:\s*\d+\s*,[^\n]*\n)+)", deck, re.M)
    written = {int(line.split(",")[0]) for line in block.group(1).splitlines() if line.strip()}
    assert orphan.id not in written, "the unsectioned element is not written"
    assert written == {el.id for el in fem.elements} - {orphan.id}, "every other element is"
    assert "no FemSection" in " ".join(r.getMessage() for r in records)
