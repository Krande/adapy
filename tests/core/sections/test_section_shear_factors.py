"""Shear factors SFY/SFZ on the Sesam profile cards (GIORH, GBOX, GLSEC, GCHAN, GBARM, GPIPE).

The Sesam Input Interface File description (89-7012) gives each profile card the factors SFY and
SFZ, "the shear area calculated by the preprocessor program is multiplied by SFY" for SHARY (SFZ,
SHARZ). GeniE V8.13-02, ``genie_v8_13_review437.js``: the six L3_* sections are S01, S04, S06,
S07, S09 and S10 of ``genie_v8_13_shear_areas.js`` with ``shearFactorY = 0.5`` and
``shearFactorZ = 0.8``. Their cards carry SFY 0.5 and SFZ 0.8, and their GBEAMG SHARY/SHARZ are
exactly 0.5000000 and 0.8000000 times the unfactored sections' (0.7999999 for the box, single
precision), every other field unchanged.

adapy read neither factor (``Sfy = Sfz = 1`` on every section read), so such a deck written back
out said SFY = 1 beside a SHARY that included 0.5, and calculated properties never applied them.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.sections.properties import calculate_general_properties

REF_DIR = pathlib.Path(__file__).resolve().parents[3] / "files/fem_files/sesam/section_props"
REVIEW_FEM = REF_DIR / "genie_v8_13_review437_T1.FEM"
SFY, SFZ = 0.5, 0.8
FACTORED = ("L3_I", "L3_BOX", "L3_ANG", "L3_CHAN", "L3_BAR", "L3_PIPE")
FIELDS = ("Ax", "Ix", "Iy", "Iz", "Wxmin", "Wymin", "Wzmin", "Shary", "Sharz", "Shceny", "Shcenz", "Sy", "Sz")


def _sections(path) -> dict[str, ada.Section]:
    a = ada.from_fem(path)
    return {s.name: s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections}


@pytest.fixture(scope="module")
def genie() -> dict[str, ada.Section]:
    return _sections(REVIEW_FEM)


@pytest.mark.parametrize("name", FACTORED)
def test_the_reader_keeps_the_shear_factors(genie, name):
    p = genie[name].properties
    assert (p.Sfy, p.Sfz) == pytest.approx((SFY, SFZ), rel=1e-7)


@pytest.mark.parametrize("name", FACTORED)
def test_calculated_shear_areas_are_multiplied_by_the_factors(genie, name):
    """adapy's own calculation with the factors equals GeniE's GBEAMG, every field (1e-6, as in
    ``test_section_shear_areas.py``)."""
    sec = genie[name]
    calc = calculate_general_properties(sec, sfy=SFY, sfz=SFZ)
    assert (calc.Sfy, calc.Sfz) == (SFY, SFZ)
    for field in FIELDS:
        c, r = getattr(calc, field), getattr(sec.properties, field)
        assert np.isclose(c, r, rtol=1e-6, atol=1e-12), f"{field}: adapy {c:.8e} GeniE {r:.8e}"


def test_the_parent_comparison_uses_the_stored_factors(genie):
    """``GeneralProperties.calc_parent_properties`` (what ``modified`` compares with) calculates with
    the factors the properties carry, so a factored SHARY is not mistaken for a modified one."""
    p = genie["L3_I"].properties
    calc = p.calc_parent_properties()
    assert np.isclose(calc.Shary, p.Shary, rtol=1e-6) and np.isclose(calc.Sharz, p.Sharz, rtol=1e-6)


def test_a_factored_deck_round_trips_through_the_sesam_writer(genie, tmp_path):
    """Read GeniE's deck, write it, read it back: the cards say SFY 0.5 / SFZ 0.8 again, beside the
    same factored GBEAMG. Before, the cards came back 1.0 under a SHARY that still included 0.5 --
    GeniE recomputing from them (as it does on import) lost the factor."""
    a = ada.from_fem(REVIEW_FEM)
    a.to_fem("rt", "sesam", scratch_dir=tmp_path, overwrite=True)
    back = _sections(tmp_path / "rt" / "rtT1.FEM")
    for name in FACTORED:
        p, g = back[name].properties, genie[name].properties
        assert (p.Sfy, p.Sfz) == pytest.approx((SFY, SFZ), rel=1e-7), name
        assert (p.Shary, p.Sharz) == pytest.approx((g.Shary, g.Sharz), rel=1e-7), name


def test_an_unfactored_section_reads_factors_of_one():
    p = _sections(REF_DIR / "genie_v8_13_shear_areas_T1.FEM")["S01_IPE300"].properties
    assert (p.Sfy, p.Sfz) == (1.0, 1.0)
