"""Torsion constant, section moduli and shear centre (GBEAMG IX, WXMIN, WYMIN, WZMIN, SHCENY,
SHCENZ) of the parametric sections.

The Sesam Input Interface File description (89-7012, GBEAMG) defines IX as the torsional
moment of inertia and WXMIN as the minimum torsional section modulus, both about the shear
centre (WXMIN = IX / rmax for a pipe), WYMIN = IY / zmax, WZMIN = IZ / ymax, and SHCENY/SHCENZ
as the y and z components of the shear centre location. It gives no formula and no origin; the
GeniE values below put the shear centre relative to the centroid (an angle's SHCENZ is the leg
intersection minus the centroid height: L200x100x10x14, 0.007 - 0.064055 = -0.057055).

GeniE (the preprocessor that normally writes GBEAMG for Sestra) is the primary reference:
``genie_v8_13_torsion.js`` adds the cases ``genie_v8_13_shear_areas*.js`` lacks (angles with
the web thicker than the flange, a wide and a square bar, an equal-wall box, a box with webs
thicker than the flanges, a second equal-thickness channel, TG650x300x25x40, and IPE300 and a
channel with root radii). What GeniE V8.13-02 writes, reconstructed exactly (to < 2e-7):

* Box: Bredt, IX = 4 Am^2 / (hb/tb + hb/tt + 2 ha/ty), Am = ha hb, ha = h - (tb + tt)/2,
  hb = b - ty; WXMIN = IX (ha + hb) / (ha hb).
* Angle: Roark's L-section formula with no root radius, IX = K1 + K2 + alpha D^4, the thicker
  leg (a, b) running through the corner and the other (c, d) ending at it,
  K1 = a b^3 (1/3 - 0.21 (b/a)(1 - b^4/(12 a^4))), K2 = c d^3 (1/3 - 0.105 (d/c)(1 - d^4/(192
  c^4))), alpha = 0.07 d/b, D = 2 (b + d - sqrt(2 b d)) the diameter of the circle inscribed in
  the corner; WXMIN = IX / D. Shear centre at the leg intersection, (tw/2, tf/2) from the heel.
* Flat bar b x t (b >= t), n = b/t: IX = (1 - 0.63/n + 0.052/n^5)/3 b t^3, WXMIN = IX/(1 -
  0.63/(1 + n^3)) / t; a square bar 0.141 a^4 and 0.208 a^3.
* Channel: WXMIN = IX / max(tw, tf); shear centre from the web's centreline e = b'^2 h'^2 tf /
  (4 Iy), b' = b - tw/2, h' = h - tf.
* I, T, pipe, solid round: what adapy already wrote.

Measured on upstream main (e1e4381be), relative to GeniE: box IX and WXMIN -9.4 % (BOX
400x300x12x20) and -10.0 % (flanges 20/30): the web thickness used for the top flange; angle IX
and WXMIN +2.3 % to +8.2 % (sum b t^3 / 3); angle SHCENZ +4.907e-2 for -5.706e-2 and WYMIN
x9.7, from a centroid height with a sign error; flat bar IX x58 and WXMIN x3.1; channel with
tw = tf: WXMIN x500 (IX / IY) and SHCENY -1.674e-2 for -4.343e-2 (a misplaced bracket made the
web-to-shear-centre term 1e-11 m); an angle with the web thicker than the flange raised
ValueError, so ``ada.from_fem`` of ``genie_v8_13_torsion_T1.FEM`` failed.

Independent references, at the end of this module: closed forms (Bredt, Roark's rectangle
series, the thin-walled channel shear centre, the leg intersection) and, in the docstrings, a
warping-function finite-element solution (sectionproperties 3.10.2, meshes of t^2/16 and
t^2/64, J converged to < 1e-3).

Tolerance: as in ``test_section_shear_areas.py`` -- GeniE stores the profile dimensions in
single precision, so everything adapy recomputes from them differs by up to ~2e-7 relative;
``REL = 1e-6``. Shear centres of symmetric sections, which GeniE writes as 0 or +-3e-17, are
compared with ``ATOL = 1e-12`` m.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.sections.properties import calculate_general_properties

REL = 1e-6
ATOL = 1e-12
REF_DIR = pathlib.Path(__file__).resolve().parents[3] / "files/fem_files/sesam/section_props"
GENIE_FEMS = (
    REF_DIR / "genie_v8_13_shear_areas_T1.FEM",
    REF_DIR / "genie_v8_13_shear_areas_edge_T1.FEM",
    REF_DIR / "genie_v8_13_torsion_T1.FEM",
)
FIELDS = ("Ix", "Wxmin", "Wymin", "Wzmin", "Shceny", "Shcenz")

# Section name in the GeniE models -> what it exercises
GENIE_SECTIONS = {
    "S01_IPE300": "doubly symmetric I (IPE300 without root radius)",
    "S02_UNSI": "asymmetric I, flanges 200x16 top, 300x20 bottom",
    "S03_TEE": "T: unsymmetrical I with the bottom flange as wide as the web",
    "S04_BOX": "box 400x300, web 12, flanges 20",
    "S05_BOXU": "box with unequal flanges, top 20, bottom 30",
    "S06_PIPE": "pipe D500 t20",
    "S07_ANG": "unequal angle 200x100, web 10, flange 14",
    "S08_ANGEQ": "equal angle 150x150x12",
    "S09_CHAN": "channel 300x100, web 7.1, flange 11",
    "S10_BAR": "flat bar 200x50",
    "S11_ROD": "solid round D100 (GPIPE with DI = 0, read as a tube of wall r)",
    "S12_IEQ": "I with web and flanges all 10 mm thick",
    "S13_CHEQ": "channel with web and flanges both 8 mm thick",
    "E1_TEETHICK": "T 100 high, flange 300x50 on a 5 mm web",
    "E2_ANGFLNA": "angle 60x300, web 5, flange 40",
    "T02_ANGTW": "angle 200x100, web 14 thicker than the flange 10",
    "T03_ANGTW2": "angle 100x200, web 16, flange 8",
    "T04_ANGSM": "equal angle 100x100x10",
    "T05_BARWIDE": "flat bar 50 high, 200 wide",
    "T06_BARSQ": "square bar 100x100",
    "T07_BOXEQ": "box 300x200, all walls 10",
    "T08_BOXTW": "box 300x500, webs 30, bottom 15, top 10",
    "T10_CHEQ2": "channel 300x90, web and flanges 10",
    "T11_TG650": "T 650x300x25x40, as adapy's TG650x300x25x40",
}

# The asymmetric-I Iy (and with it Wymin) is fixed by the shear-area change (Krande/adapy#437,
# "asymmetric I Iy used the bottom flange thickness in the top flange term"): main writes Wymin
# 2.053078e-3 for GeniE's 2.052856e-3 (S02) and 9.0315e-6 for 5.1116e-5 (E1). Strict, so the
# marks must go once that change is in.
NEEDS_437 = {("S02_UNSI", "Wymin"), ("E1_TEETHICK", "Wymin")}


class _GenieSections(dict):
    """Section name -> section, each FEM read on first use (``S``/``E`` names from the shear-area
    files, ``T`` names from the torsion file), so a file adapy cannot read fails its own cases only."""

    def __missing__(self, name):
        fem = GENIE_FEMS[2] if name.startswith("T") else GENIE_FEMS[1] if name.startswith("E") else GENIE_FEMS[0]
        a = ada.from_fem(fem)
        self.update({s.name: s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections})
        return self[name] if name in self else None


@pytest.fixture(scope="module")
def genie_sections() -> dict[str, ada.Section]:
    return _GenieSections()


def _params():
    for name in GENIE_SECTIONS:
        for field in FIELDS:
            marks = ()
            if (name, field) in NEEDS_437:
                marks = pytest.mark.xfail(strict=True, reason="asymmetric-I Iy, fixed in Krande/adapy#437")
            yield pytest.param(name, field, marks=marks, id=f"{name}-{field}")


@pytest.mark.parametrize("name, field", list(_params()))
def test_torsion_and_shear_centre_match_genie(genie_sections, name, field):
    """adapy's own value, from the profile record alone, equals GeniE's GBEAMG."""
    sec = genie_sections[name]
    c, r = getattr(calculate_general_properties(sec), field), getattr(sec.properties, field)
    assert np.isclose(c, r, rtol=REL, atol=ATOL), f"{field}: adapy {c:.6e} GeniE {r:.6e}"


def test_the_genie_files_hold_every_section(genie_sections):
    """The parametrisation above silently covers nothing if a profile is not read back."""
    assert all(genie_sections[name] is not None for name in GENIE_SECTIONS)


def test_reference_file_is_genie_output():
    head = GENIE_FEMS[2].read_text().splitlines()[3]
    assert "SESAM GeniE" in head and "V8.13-02" in head


def test_tg_string_matches_genie(genie_sections):
    """``TG650x300x25x40`` (bottom flange collapsed to the web) against GeniE's same T."""
    sec = ada.Section("TG", from_str="TG650x300x25x40")
    p, g = calculate_general_properties(sec), genie_sections["T11_TG650"].properties
    for field in ("Ax", "Iy", "Iz") + FIELDS:
        c, r = getattr(p, field), getattr(g, field)
        assert np.isclose(c, r, rtol=REL, atol=ATOL), f"{field}: adapy {c:.6e} GeniE {r:.6e}"


# --- independent of GeniE --------------------------------------------------------------------


def _box(h, b, tw, tb, tt):
    return ada.Section("B", sec_type="BG", h=h, w_top=b, w_btn=b, t_w=tw, t_fbtn=tb, t_ftop=tt)


@pytest.mark.parametrize("h, b, tw, tb, tt", [(0.4, 0.3, 0.012, 0.03, 0.02), (0.3, 0.5, 0.03, 0.015, 0.01)])
def test_box_torsion_is_bredt(h, b, tw, tb, tt):
    """Bredt for a single cell, J = 4 Am^2 / (closed integral of ds / t), every wall at its own
    thickness. Main used the web thickness for the top flange: 4.8549e-4 for Bredt's 5.3938e-4
    (-10.0 %) and 1.1038e-3 for 7.4908e-4 (+47 %). Warping FE (sectionproperties) of the solid
    walls: 5.5729e-4 and 7.7529e-4, 3.3 % and 3.5 % above thin-walled Bredt."""
    ha, hb = h - (tb + tt) / 2, b - tw
    bredt = 4 * (ha * hb) ** 2 / (hb / tb + hb / tt + 2 * ha / tw)
    assert np.isclose(calculate_general_properties(_box(h, b, tw, tb, tt)).Ix, bredt, rtol=1e-12)


def test_an_angle_with_its_web_thicker_than_its_flange_is_calculated():
    """Main raised ValueError("Currently not implemented this yet") for tw > tf, which made
    ``ada.from_fem`` fail on any Sesam file holding such an angle (GeniE writes them)."""
    sec = ada.Section("L", sec_type="L", h=0.2, w_btn=0.1, w_top=0.1, t_w=0.014, t_fbtn=0.01, t_ftop=0.01)
    assert calculate_general_properties(sec).Ix > 0


@pytest.mark.parametrize(
    "h, b, tw, tf", [(0.2, 0.1, 0.01, 0.014), (0.15, 0.15, 0.012, 0.012), (0.18, 0.035, 0.01, 0.01975)]
)
def test_angle_shear_centre_is_the_leg_intersection(h, b, tw, tf):
    """Thin-walled angle: both legs' shear flows pass through the intersection of their
    midlines, (tw/2, tf/2) from the heel; SHCENY/SHCENZ are that point minus the centroid, and
    WYMIN is Iy over the larger of the centroid's distances to the heel and to the web tip.
    Main put the centroid height at -0.04207 for L200x100x10x14 (centroid 0.06406), so SHCENZ
    came out +4.907e-2 for -5.706e-2. Warping FE (sectionproperties) of the solid L: -5.607e-2,
    1.7 % from the thin-walled point."""
    sec = ada.Section("L", sec_type="L", h=h, w_btn=b, w_top=b, t_w=tw, t_fbtn=tf, t_ftop=tf)
    web, flange = tw * (h - tf), b * tf
    cy = (web * tw / 2 + flange * b / 2) / (web + flange)
    cz = (web * (tf + (h - tf) / 2) + flange * tf / 2) / (web + flange)
    p = calculate_general_properties(sec)
    assert np.isclose(p.Shceny, tw / 2 - cy, rtol=1e-12)
    assert np.isclose(p.Shcenz, tf / 2 - cz, rtol=1e-12)
    assert np.isclose(p.Wymin, p.Iy / max(cz, h - cz), rtol=1e-12)
