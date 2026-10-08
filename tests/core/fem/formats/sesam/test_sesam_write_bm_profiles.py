"""Beam profile records the Sesam writer used to fall over on.

Two section types adapy understands perfectly well could not be written at all:
CHANNEL, because Sesam's GCHAN card has no writer here and the "export as general
section only" path called ``None``; and CIRCULAR, because a solid round bar has no
wall thickness to put in the GPIPE record it is written as. Both crashed in the
middle of writing a deck, taking the rest of the model with them.
"""

from __future__ import annotations

import pathlib

import numpy as np

import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_bm_profiles import write_bm_section
from ada.sections.properties import calculate_general_properties

GENIE_FEM = (
    pathlib.Path(__file__).resolve().parents[5] / "files/fem_files/sesam/section_props/genie_v8_13_shear_areas_T1.FEM"
)


def _deck_for(section: str, tmp_path):
    bm = ada.Beam("B", (0, 0, 0), (4, 0, 0), section)
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(5.0, "line")
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / "m" / "mT1.FEM").read_text()


def test_channel_writes_gchan_and_reads_back(tmp_path):
    """A channel is a GCHAN (manual 7.3.4) beside its GBEAMG stiffness; it used to be the
    GBEAMG alone, and read back as a general section."""
    text = _deck_for("UNP180x10", tmp_path)
    match = cards.GBEAMG.to_ff_re().search(text)
    assert match is not None
    # The stiffness is really there, not a record of zeros: UNP180 has an area of
    # about 2.8e-3 m2.
    assert float(match.groupdict()["area"]) > 1e-3

    sec = ada.Section.from_str("UNP180x10")
    back = next(iter(ada.from_fem(tmp_path / "m" / "mT1.FEM").get_by_name("T1").sections))
    assert back.type == sec.type
    assert (back.h, back.w_top, back.w_btn, back.t_w, back.t_ftop, back.t_fbtn) == (
        sec.h,
        sec.w_top,
        sec.w_btn,
        sec.t_w,
        sec.t_ftop,
        sec.t_fbtn,
    )


def test_circular_is_a_gpipe_with_no_bore(tmp_path):
    """A solid bar is GPIPE with DI = 0 and T = r, as GeniE V8.13-02 writes ``PipeSection(D, D/2)``
    (S11_ROD in files/fem_files/sesam/section_props/genie_v8_13_shear_areas_T1.FEM: DI 0, DY 0.1,
    T 0.05) and as Sestra V11.3 runs (tests/fem/test_sesam_solid_round.py). It used to be a tube
    with a bore of 1 % of the diameter, reported as an approximation."""
    with conversion_report.collect() as report:
        text = _deck_for("CIRC100", tmp_path)
    match = cards.re_gpipe.search(text)
    assert match is not None
    d = match.groupdict()
    assert (float(d["di"]), float(d["dy"]), float(d["t"])) == (0.0, 0.2, 0.1)  # CIRC100 -> r = 0.1 m
    assert not [f for f in report.findings if f.keyword == "Section"]


def test_circular_reads_back_as_the_same_disc(tmp_path):
    """CIRC100 written and read back is CIRC100: the GPIPE without a bore is read as a CIRCULAR
    section, and the GBEAMG it carries is what adapy calculates for that section. With the 1 %
    bore it came back a TUBULAR of wall 0.099 whose own Shary/Sharz (2.33263e-2) were 1.0 % below
    the GBEAMG's disc values (2.35619e-2)."""
    _deck_for("CIRC100", tmp_path)
    back = next(iter(ada.from_fem(tmp_path / "m" / "mT1.FEM").get_by_name("T1").sections))
    assert back.type == back.TYPES.CIRCULAR and back.r == 0.1
    stored, calc = back.properties, calculate_general_properties(back)
    original = ada.Section.from_str("CIRC100").properties
    for field in ("Ax", "Ix", "Iy", "Iz", "Wxmin", "Wymin", "Wzmin", "Shary", "Sharz", "Sy", "Sz"):
        assert np.isclose(getattr(stored, field), getattr(original, field), rtol=1e-7), field  # GBEAMG, 8 digits
        assert np.isclose(getattr(calc, field), getattr(stored, field), rtol=1e-7), field


def test_genie_solid_rod_reads_as_circular():
    a = ada.from_fem(GENIE_FEM)
    (rod,) = [s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections if s.name == "S11_ROD"]
    assert rod.type == rod.TYPES.CIRCULAR and np.isclose(rod.r, 0.05, rtol=1e-7)


def test_a_supported_profile_still_writes_its_own_card():
    """Guard the early return added for the unsupported types: the normal path, where
    a writer exists, must still emit the profile card after the GBEAMG."""
    sec = ada.Section.from_str("HEA300")
    out = write_bm_section(sec, 1)
    assert "GBEAMG" in out and "GIORH" in out
