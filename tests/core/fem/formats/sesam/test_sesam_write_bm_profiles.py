"""Beam profile records the Sesam writer used to fall over on.

Two section types adapy understands perfectly well could not be written at all:
CHANNEL, because Sesam's GCHAN card has no writer here and the "export as general
section only" path called ``None``; and CIRCULAR, because a solid round bar has no
wall thickness to put in the GPIPE record it is approximated by. Both crashed in the
middle of writing a deck, taking the rest of the model with them.
"""

from __future__ import annotations

import ada
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_bm_profiles import write_bm_section


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


def test_circular_writes_a_thickness_instead_of_none(tmp_path):
    """A solid bar is written as GPIPE with a 1% inner diameter; the wall follows."""
    text = _deck_for("CIRC100", tmp_path)
    match = cards.re_gpipe.search(text)
    assert match is not None
    d = match.groupdict()
    di, dy, t = float(d["di"]), float(d["dy"]), float(d["t"])
    assert dy == 0.2  # CIRC100 -> r = 0.1 m
    assert t == (dy - di) / 2


def test_a_supported_profile_still_writes_its_own_card():
    """Guard the early return added for the unsupported types: the normal path, where
    a writer exists, must still emit the profile card after the GBEAMG."""
    sec = ada.Section.from_str("HEA300")
    out = write_bm_section(sec, 1)
    assert "GBEAMG" in out and "GIORH" in out
