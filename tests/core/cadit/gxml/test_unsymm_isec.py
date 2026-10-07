import xml.etree.ElementTree as ET

import pytest

from ada import Section
from ada.cadit.gxml.read.read_sections import unsymm_isec


def _make(**attrib):
    el = ET.Element("unsymmetrical_i_section", attrib={k: str(v) for k, v in attrib.items()})
    return el


def test_asymmetric_i_stays_iprofile():
    el = _make(h=0.8, bfbot=0.3, bftop=0.2, tw=0.012, tftop=0.015, tfbot=0.02)
    sec = unsymm_isec("AI800x300x200x12x20x15", el)
    assert sec.type == Section.TYPES.IPROFILE


def test_roundtripped_tprofile_recovered():
    # Adapy's TPROFILE export: tftop=tfbot, tw=bfbot.
    el = _make(h=0.5, bfbot=0.012, bftop=0.2, tw=0.012, tftop=0.02, tfbot=0.02)
    sec = unsymm_isec("T500x200x12x20", el)
    assert sec.type == Section.TYPES.TPROFILE
    assert sec.w_top == pytest.approx(0.2)
    assert sec.t_ftop == pytest.approx(0.02)


def test_inverted_t_flange_down_flipped_to_tprofile():
    # Audit-#5256 shape: wide bottom flange, degenerate top.
    el = _make(h=1.0, bfbot=0.4, bftop=0.025, tw=0.025, tftop=0.0001, tfbot=0.03)
    sec = unsymm_isec("T1000x400x25x30", el)
    assert sec.type == Section.TYPES.TPROFILE
    # Flipped into adapy convention (flange-up), so the wide bottom
    # becomes the TPROFILE top flange.
    assert sec.w_top == pytest.approx(0.4)
    assert sec.t_ftop == pytest.approx(0.03)


@pytest.mark.parametrize("down", [False, True], ids=["flange-up", "flange-down"])
def test_a_t_reads_with_adapys_web_wide_stub(down):
    """The paper-thin flange (GeniE's library T, 0.001 mm; a manual T, 0.1 mm) is read as the stub
    adapy keeps for a T -- as wide as the web, as thick as the flange -- so a T written by adapy
    reads back with the dimensions it had."""
    real = dict(b=0.4, t=0.03)
    absent = dict(b=0.025 + 1e-6, t=1e-6)
    top, bot = (absent, real) if down else (real, absent)
    el = _make(h=1.0, tw=0.025, bftop=top["b"], tftop=top["t"], bfbot=bot["b"], tfbot=bot["t"])
    sec = unsymm_isec("T", el)
    assert sec.type == Section.TYPES.TPROFILE
    assert (sec.w_top, sec.t_ftop, sec.w_btn, sec.t_fbtn) == pytest.approx((0.4, 0.03, 0.025, 0.03))
