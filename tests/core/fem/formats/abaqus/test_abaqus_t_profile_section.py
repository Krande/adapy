"""A T-profile as an Abaqus ``*Beam Section``.

A T is in :class:`~ada.sections.categories.BaseTypes` and reaches every other writer, but the
Abaqus INP writer had no mapping for one: ``line_cross_sec_type_str`` raised
*Section type "T" is not added to Abaqus beam export yet*. A T stiffener on a plate is ordinary, so
a model carrying one could not be analysed through Abaqus or Calculix at all.

Abaqus has no ``section=T``. Its own preprocessor spells a T as ``section=I`` with the bottom flange
zeroed, and that is the encoding written here.

The zeroing is the substance of the test rather than a detail. adapy stores a T in the
*collapsed-bottom-flange* form -- ``w_btn = t_w`` and ``t_fbtn = t_ftop``, set that way in
``string_to_section`` and ``from_geometry`` because ``None`` breaks the Genie XML writer's
arithmetic. Handing those two fields to the ordinary I-section data line is therefore not a harmless
shortcut: it describes a T with a stub flange ``t_w`` wide and ``t_ftop`` thick along the bottom,
which is material the model does not have and stiffness it does not have, with nothing raised
anywhere.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem.formats.abaqus.write.write_sections import t_as_i_section_lines
from ada.sections.categories import BaseTypes


@pytest.fixture
def tee() -> ada.Section:
    """A welded T: 650 deep, a 300 x 40 top flange, a 25 web."""
    return ada.Section("TG650", from_str="TG650x300x25x40")


def test_the_stored_t_really_is_the_collapsed_flange_form(tee):
    """Precondition for everything below: the fields the naive I line would have read."""
    assert tee.type == BaseTypes.TPROFILE
    assert tee.w_btn == tee.t_w, "adapy collapses a T's bottom flange width onto the web thickness"
    assert tee.t_fbtn == tee.t_ftop, "and its bottom flange thickness onto the top one"


def test_the_bottom_flange_is_written_as_absent(tee):
    """``b1`` and ``t1`` are zero -- the kernel's own way of saying there is no bottom flange."""
    dims = [float(v) for v in t_as_i_section_lines(tee).split(",")]

    assert len(dims) == 7, "section=I takes l, h, b1, b2, t1, t2, t3"
    assert dims[2] == 0.0, "b1, the bottom flange width"
    assert dims[4] == 0.0, "t1, the bottom flange thickness"


def test_the_web_and_top_flange_are_the_sections_own(tee):
    dims = [float(v) for v in t_as_i_section_lines(tee).split(",")]
    l_, h, _b1, b2, _t1, t2, t3 = dims

    assert h == pytest.approx(tee.h)
    assert l_ == pytest.approx(tee.h / 2), "adapy's outlines are centred on the beam axis"
    assert b2 == pytest.approx(tee.w_top)
    assert t2 == pytest.approx(tee.t_ftop)
    assert t3 == pytest.approx(tee.t_w)


def test_the_stub_flange_the_naive_line_would_have_written_is_not_there(tee):
    """The failure mode in numbers: reading ``w_btn``/``t_fbtn`` gives a 25 x 40 stub flange."""
    dims = [float(v) for v in t_as_i_section_lines(tee).split(",")]

    assert dims[2] != pytest.approx(tee.w_btn), "b1 must not be the collapsed web thickness"
    assert dims[4] != pytest.approx(tee.t_fbtn), "t1 must not be the top flange's thickness"


def test_a_t_reaches_a_written_deck(tmp_path, tee):
    """End to end: a T-stiffened member goes out as a deck instead of raising."""
    bm = ada.Beam("BM", (0, 0, 0), (2, 0, 0), sec=tee)
    p = ada.Part("P") / bm
    a = ada.Assembly("A") / p

    p.fem = p.to_fem_obj(mesh_size=100.0, experimental_bm_splitting=False)
    a.to_fem("tee", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    deck = (tmp_path / "tee" / "tee.inp").read_text()

    lines = deck.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("*Beam Section"))
    assert "section=I" in lines[idx], f"a T goes out on the I keyword; got {lines[idx]!r}"

    dims = [float(v) for v in lines[idx + 1].split(",")]
    assert dims[2] == 0.0 and dims[4] == 0.0, f"with the bottom flange zeroed; its data line was {dims}"
    assert dims[1] == pytest.approx(tee.h)
    assert dims[3] == pytest.approx(tee.w_top)
