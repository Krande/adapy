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
