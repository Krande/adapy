"""The Sesam super element number: where it comes from, and that the file and the deck agree.

Sesam names an input interface file ``<prefix>T<n>.FEM`` and expects the deck's own IDENT record to
carry the same ``n`` as ``SELTYP``; Presel matches the two when it assembles. Before this, adapy
wrote ``SELTYP`` as a hardcoded 1 whatever the file was called, so a deck named ``…T10.FEM`` was
read by Presel as super element 1 — a wrong assembly with nothing in the output saying so.

The reference for the field layout is Presel's own files (``IDENT SLEVEL SELTYP SELMOD``):

    T1.FEM      IDENT  1.0     1.0  3.0
    T100.FEM    IDENT  1.0   100.0  3.0
    T1000.FEM   IDENT  2.0  1000.0  0.0     <- the assembly, so SLEVEL 2

adapy writes a single first-level super element, so SLEVEL stays 1 and only SELTYP is ours to set.
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
    _deck_name,
    _resolve_superelement,
    _sesam_superelement,
)
from ada_cli import CliUsageError


def _ns(input_file, output_file, superelement=None, to_format=None) -> argparse.Namespace:
    return argparse.Namespace(
        input=str(input_file),
        output=str(output_file),
        from_format=None,
        to_format=to_format,
        split=False,
        limit=None,
        strict=False,
        superelement=superelement,
    )


@contextlib.contextmanager
def _ada_logs(level: int = logging.DEBUG):
    """Collect ``ada`` log records; its logger does not propagate, so caplog cannot see them."""
    from ada.config import logger

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect(level)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.fixture(scope="module")
def src_inp(tmp_path_factory) -> pathlib.Path:
    """A small Abaqus deck the Sesam writer can re-emit."""
    tmp = tmp_path_factory.mktemp("seltyp_src")
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("A") / (ada.Part("P", fem=bm.to_fem_obj(0.25, "shell")) / bm)
    a.to_fem("src", fem_format="abaqus", scratch_dir=tmp, overwrite=True, write_input_files_only=True)
    return tmp / "src" / "src.inp"


def _ident(deck: pathlib.Path) -> list[str]:
    """The IDENT record's fields: ``[SLEVEL, SELTYP, SELMOD, _]``."""
    first = deck.read_text(errors="replace").splitlines()[0].split()
    assert first[0] == "IDENT", first
    return first[1:]


# --------------------------------------------------------------------------------------
# Reading the number off the name
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stem,number,name",
    [
        ("myPrefixT10", 10, "myPrefix"),
        ("modelT1", 1, "model"),  # the spelling Sesam itself uses; must keep working
        ("myT1000", 1000, "my"),
        ("lowert7", 7, "lower"),  # Windows names are case-insensitive, so accept a lower-case t
        ("T100", 100, "T100"),  # nothing left to keep as a prefix, so the stem stands
        ("part", None, "part"),
        ("beamT3_rev2", None, "beamT3_rev2"),  # the T-number must end the stem, not sit inside it
    ],
)
def test_the_t_number_in_the_name_is_the_super_element_number(stem, number, name):
    out = pathlib.Path(f"{stem}.FEM")
    assert _sesam_superelement(out) == number
    assert _deck_name(out, "sesam") == name


def test_only_sesam_reads_a_t_number_off_the_name():
    """``R<n>`` is results and ``L<n>`` loads; and no other writer has this convention at all."""
    out = pathlib.Path("modelT10.inp")
    assert _deck_name(out, "abaqus") == "modelT10"


# --------------------------------------------------------------------------------------
# Resolution, and saying which number was chosen
# --------------------------------------------------------------------------------------


def test_a_name_without_a_number_says_which_one_it_will_use():
    """An implicit default has to announce itself. A silently defaulted 1 in a deck the user meant
    to be 10 is precisely the mismatch this resolution exists to prevent."""
    with _ada_logs() as records:
        assert _resolve_superelement(pathlib.Path("part.FEM"), None) == 1

    said = [r.getMessage() for r in records]
    assert any("super element 1" in m for m in said), said
    assert any("--superelement" in m for m in said), "the message should say how to choose"


def test_a_name_with_a_number_says_where_it_came_from():
    with _ada_logs() as records:
        assert _resolve_superelement(pathlib.Path("myPrefixT10.FEM"), None) == 10

    assert any("10" in r.getMessage() and "myPrefixT10.FEM" in r.getMessage() for r in records)


def test_an_explicit_number_is_used_when_the_name_is_silent():
    assert _resolve_superelement(pathlib.Path("part.FEM"), 100) == 100


def test_an_explicit_number_agreeing_with_the_name_is_fine():
    assert _resolve_superelement(pathlib.Path("myPrefixT10.FEM"), 10) == 10


def test_an_explicit_number_contradicting_the_name_is_refused():
    """Not a warning: honouring either number leaves a file whose name says one super element and
    whose IDENT says another, which is the whole failure being fixed."""
    with pytest.raises(CliUsageError) as exc:
        _resolve_superelement(pathlib.Path("myPrefixT10.FEM"), 7)

    message = str(exc.value)
    assert "contradicts" in message
    assert "myPrefixT7.FEM" in message, "the error should name the output that would satisfy it"


def test_a_number_below_one_is_refused():
    with pytest.raises(CliUsageError):
        _resolve_superelement(pathlib.Path("part.FEM"), 0)


# --------------------------------------------------------------------------------------
# End to end: the deck and its name carry the same number
# --------------------------------------------------------------------------------------


def test_the_deck_named_t10_is_super_element_10(src_inp, tmp_path):
    out = tmp_path / "myPrefixT10.FEM"
    assert _cmd_convert(_ns(src_inp, out)) == 0

    assert out.is_file()
    slevel, seltyp, selmod = _ident(out)[:3]
    assert float(seltyp) == 10.0
    assert float(slevel) == 1.0, "adapy writes a first-level super element"
    assert float(selmod) == 3.0, "a 3-dimensional model"


def test_a_deck_with_no_number_in_its_name_is_super_element_one(src_inp, tmp_path):
    out = tmp_path / "part.FEM"
    assert _cmd_convert(_ns(src_inp, out)) == 0

    assert float(_ident(out)[1]) == 1.0


def test_an_explicit_flag_sets_the_number_of_a_plainly_named_deck(src_inp, tmp_path):
    out = tmp_path / "part.FEM"
    assert _cmd_convert(_ns(src_inp, out, superelement=100)) == 0

    assert float(_ident(out)[1]) == 100.0


def test_a_contradiction_is_refused_before_the_input_is_read(src_inp, tmp_path, monkeypatch):
    """Reading an 81 MB deck takes a minute; a usage error must not cost it first."""
    from ada.api import cli as cli_module

    def _must_not_run(*args, **kwargs):
        raise AssertionError("the input was read before the contradiction was caught")

    monkeypatch.setattr(cli_module, "_load", _must_not_run)

    with pytest.raises(CliUsageError):
        _cmd_convert(_ns(src_inp, tmp_path / "myPrefixT10.FEM", superelement=7))


def test_t1_stays_exactly_as_it_was(src_inp, tmp_path):
    """The pre-existing spelling: ``modelT1.FEM`` must not become ``modelT1T1.FEM`` and must stay
    super element 1, because decks already written that way are in use."""
    out = tmp_path / "modelT1.FEM"
    assert _cmd_convert(_ns(src_inp, out)) == 0

    assert sorted(p.name for p in tmp_path.iterdir()) == ["modelT1.FEM"]
    assert float(_ident(out)[1]) == 1.0


# --------------------------------------------------------------------------------------
# The library entry point
# --------------------------------------------------------------------------------------


def test_the_writer_names_the_file_after_the_number(tmp_path):
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("A") / (ada.Part("P", fem=bm.to_fem_obj(0.25, "shell")) / bm)
    a.to_fem(
        "m",
        fem_format="sesam",
        scratch_dir=tmp_path,
        overwrite=True,
        write_input_files_only=True,
        metadata={"sesam_superelement": 42},
    )

    deck = tmp_path / "m" / "mT42.FEM"
    assert deck.is_file(), sorted(p.name for p in (tmp_path / "m").iterdir())
    assert float(_ident(deck)[1]) == 42.0


def test_without_the_key_the_writer_is_unchanged(tmp_path):
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("A") / (ada.Part("P", fem=bm.to_fem_obj(0.25, "shell")) / bm)
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    deck = tmp_path / "m" / "mT1.FEM"
    assert deck.is_file()
    assert float(_ident(deck)[1]) == 1.0


@pytest.mark.parametrize("bad", [0, -1, 2.5, "x", True, None.__class__])
def test_a_super_element_number_that_is_not_a_whole_number_from_one_raises(tmp_path, bad):
    """Caller error, not a lossy conversion: a bad number would name the file *and* misidentify
    the deck inside it, and Presel would read some other super element entirely."""
    from ada.fem.formats.sesam.write.writer import _superelement_number

    with pytest.raises(ValueError):
        _superelement_number(bad)
