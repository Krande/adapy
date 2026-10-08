"""Shear areas (GBEAMG SHARY/SHARZ) and static moments (SY/SZ) of the parametric sections.

The Sesam Input Interface File description (89-7012, GBEAMG and the GIORH/GBOX/GPIPE/GLSEC/
GCHAN/GBARM records) defines SHARY/SHARZ as the shear areas "calculated by the preprocessor
program" times SFY/SFZ, and SY/SZ as the static area moments; it gives no formula. The
preprocessor is GeniE, so GeniE is the reference: ``genie_v8_13_shear_areas.js`` builds one
beam per section in GeniE V8.13-02 and exports ``genie_v8_13_shear_areas_T1.FEM``. Each test
reads GeniE's GBEAMG from that file and compares adapy's own calculation, from the profile
record alone, with it.

What GeniE writes is the classic thin-walled shear area A_s = I * t / S, with S the first
moment of the area on one side of the neutral axis and t the wall thickness the neutral axis
cuts (the web(s) for SHARZ, the flange(s) for SHARY; for a neutral axis inside a flange see
``test_neutral_axis_in_the_flange_matches_genie``), e.g. for IPE300 (h 0.300, b 0.150,
tw 0.0071, tf 0.0107 m, no root radius):

* Sy = b tf (h - tf)/2 + tw (h/2 - tf)^2 / 2 = 3.0105e-4 m^3; GeniE 3.01049e-4
* Sharz = Iy tw / Sy = 7.9990e-5 * 0.0071 / 3.0105e-4 = 1.8865e-3 m^2; GeniE 1.88650e-3
  (web area (h - 2 tf) tw = 1.978e-3). adapy wrote Sy = Iy / (b/2) = 1.0665e-3 and
  Sharz = 5.3250e-4, 3.5 times too small.
* Sz = 2 tf b^2 / 8 + (h - 2 tf) tw^2 / 8 = 6.1943e-5, Shary = Iz 2 tf / Sz = 2.0822e-3; GeniE
  6.19430e-5 and 2.08222e-3 (adapy already right).

Measured on upstream main (885e349c7), relative to GeniE: I/H/T Sharz -54 % to -77 % (wrong
Sy); box Shary -40 % (BOX 400x300x12x20) and -52 % (flanges 20/30) -- the web thickness 2 tw
used where GeniE cuts the two flanges, tt + tb; angle Sharz -56 % (L200x100x10x14) and -62 %
(L150x150x12) -- wrong Sy and the flange thickness used where GeniE cuts the web; asymmetric I
Iy +1.08e-4 (bottom flange thickness in the top flange's own term); solid circle Shary/Sharz
-1.0 % (a tube of wall 0.99 r stood in for the disc). Pipe, channel and flat bar already
matched.

Tolerance: GeniE stores the profile dimensions in single precision (0.3 m is written as
3.00000012E-01), so every property adapy recomputes from them differs from GeniE's by up to
1.7e-7 relative (the largest such difference in this file, IPE300 Iz, on fields that were
already right). ``REL = 1e-6`` leaves a factor of six over that, and is 100 times tighter than
the smallest defect found (the 1.08e-4 asymmetric-I Iy).
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.fem.formats import conversion_report
from ada.sections.properties import calculate_general_properties

REL = 1e-6
REF_DIR = pathlib.Path(__file__).resolve().parents[3] / "files/fem_files/sesam/section_props"
GENIE_FEM = REF_DIR / "genie_v8_13_shear_areas_T1.FEM"
EDGE_FEM = REF_DIR / "genie_v8_13_shear_areas_edge_T1.FEM"
FIELDS = ("Ax", "Iy", "Iz", "Sy", "Sz", "Shary", "Sharz")

# Section name in the GeniE model -> what it exercises
GENIE_SECTIONS = {
    "S01_IPE300": "doubly symmetric I (IPE300 without root radius)",
    "S02_UNSI": "asymmetric I, flanges 200x16 top, 300x20 bottom",
    "S03_TEE": "unsymmetrical I with a 10 x 15 bottom flange as wide as the web",
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
}


@pytest.fixture(scope="module")
def genie_sections() -> dict[str, ada.Section]:
    a = ada.from_fem(GENIE_FEM)
    return {s.name: s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections}


def _assert_matches(calculated, reference, fields=FIELDS):
    bad = []
    for field in fields:
        c, r = getattr(calculated, field), getattr(reference, field)
        if not np.isclose(c, r, rtol=REL, atol=0.0):
            bad.append(f"{field}: adapy {c:.6e} GeniE {r:.6e} ({(c - r) / r:+.3e})")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("name", list(GENIE_SECTIONS), ids=list(GENIE_SECTIONS))
def test_section_properties_match_genie(genie_sections, name):
    """adapy's Ax, Iy, Iz, Sy, Sz, Shary, Sharz for each GeniE profile equal GeniE's GBEAMG."""
    sec = genie_sections[name]
    genie = sec.properties  # GBEAMG as GeniE wrote it
    _assert_matches(calculate_general_properties(sec), genie)


def test_the_genie_file_holds_every_section(genie_sections):
    """The parametrisation above silently covers nothing if a profile is not read back."""
    assert set(GENIE_SECTIONS) <= set(genie_sections)


def test_tprofile_is_s03_tee_without_its_stub_flange(genie_sections):
    """S03_TEE is a GeniE unsymmetrical I whose bottom flange is a 10 x 15 stub as wide as the web,
    and GeniE counts that stub as a flange in SHARY: Iz (tt + tb) / Sz = 3.82768e-3. adapy's TPROFILE
    of the same dimensions is a T (the stub is web), so it has the same Ax, Iy, Iz, Sy, Sz and
    Sharz and half the Shary, Iz tt / Sz = 1.91384e-3 -- what GeniE writes for its own T
    (``test_section_tprofile.py``). Before, the TPROFILE equalled S03_TEE, doubled Shary included."""
    tee = ada.Section("T300", sec_type="TG", h=0.3, w_top=0.2, w_btn=0.01, t_w=0.01, t_ftop=0.015, t_fbtn=0.015)
    assert tee.type == tee.TYPES.TPROFILE
    s03 = genie_sections["S03_TEE"].properties
    p = calculate_general_properties(tee)
    _assert_matches(p, s03, fields=("Ax", "Iy", "Iz", "Sy", "Sz", "Sharz"))
    assert np.isclose(p.Shary, s03.Shary / 2, rtol=REL)


def test_circular_matches_genie_solid_round(genie_sections):
    """CIRCULAR r = 0.05 against GeniE's solid D100. Closed form: Sy = 2 r^3 / 3 = 8.3333e-5,
    Shary = Iz 2r / Sy = 3/4 pi r^2 = 5.8905e-3; GeniE 8.33333e-5 and 5.89049e-3."""
    rod = ada.Section("R50", sec_type="CIRC", r=0.05)
    p = calculate_general_properties(rod)
    _assert_matches(p, genie_sections["S11_ROD"].properties)
    assert np.isclose(p.Shary, 0.75 * np.pi * 0.05**2, rtol=1e-12)


def test_ipe300_sharz_is_of_the_order_of_the_web_area():
    """The defect as reported: IPE300's vertical shear area came out at 5.3250e-4, a quarter
    of the web. Closed form Iy tw / Sy = 1.8865e-3 (module docstring)."""
    sec = ada.Section("IPE300", from_str="IPE300")
    p = calculate_general_properties(sec)
    web = (sec.h - sec.t_ftop - sec.t_fbtn) * sec.t_w
    assert 0.9 < p.Sharz / web < 1.0
    assert np.isclose(p.Sharz, p.Iy * sec.t_w / p.Sy, rtol=1e-12)


@pytest.mark.parametrize(
    "name, edge",
    [
        ("E1_TEETHICK", "T 100 high, flange 300x50 on a 5 mm web: neutral axis inside the flange"),
        ("E2_ANGFLNA", "angle 60x300, web 5, flange 40: neutral axis inside the flange"),
    ],
)
def test_neutral_axis_in_the_flange_matches_genie(edge_sections, name, edge):
    """When the neutral axis lies in a flange GeniE keeps its thin-walled formulas: it still
    multiplies by the web thickness (E1, E2: Sharz / (Iy / Sy) = 0.0050000), and its Sy takes
    the web as reaching the neutral axis, which is not the true first moment there.

    E1 (z = 0.074180 from the bottom, flange from 0.05): GeniE Sy 1.37568e-5 =
    bt tt (h - tt/2 - z) + tw (h - tt - z)^2 / 2; the first moment of the area above the axis
    is 0.3 (0.1 - z)^2 / 2 = 1.0000e-4. E2 (z = 0.020248, flange to 0.04): GeniE Sy 3.95057e-6
    = tw (h - z)^2 / 2; the true value is 6.1497e-5. adapy follows GeniE.
    """
    sec = edge_sections[name]
    _assert_matches(calculate_general_properties(sec), sec.properties)


E1_FLIPPED = ada.Section(
    "E1_FLIPPED", sec_type="IG", h=0.1, w_top=0.005, t_ftop=0.005, w_btn=0.3, t_fbtn=0.05, t_w=0.005
)


@pytest.mark.parametrize(
    "name, first_moment",
    [
        ("E1_TEETHICK", 0.3 * (0.1 - 0.0741803) ** 2 / 2),
        ("E2_ANGFLNA", 0.3 * 0.0202479**2 / 2),
        ("E1_FLIPPED", 0.3 * (0.1 - 0.0741803) ** 2 / 2),
    ],
    ids=["T-top-flange", "angle-flange", "I-bottom-flange"],
)
def test_neutral_axis_in_the_flange_is_reported(edge_sections, name, first_moment, ada_warnings):
    """Matching GeniE there is a choice, so it is said: an ``approximated`` finding in the conversion
    report (logged as a warning) naming the section, with GeniE's Sy and Sharz beside the first
    moment at the axis. E1: Sy 1.37568e-5 against 1.0000e-4, Sharz 1.37817e-3 where the
    energy-consistent shear area is 8.2e-3 (sectionproperties 3.10.2); E2: 3.95057e-6 against
    6.1497e-5 (the flange below the axis, 0.3 z^2 / 2). E1 upside down (an I with a 5 x 5 top
    flange): the axis in the bottom flange, z = 0.1 - 0.0741803, first moment 0.3 z^2 / 2."""
    sec = edge_sections[name] if name in edge_sections else E1_FLIPPED
    with conversion_report.collect() as report:
        p = calculate_general_properties(sec)
    (finding,) = report.of_kind("approximated")
    assert finding.subject == name and finding.keyword == "Section"
    assert finding.details["Sy"] == p.Sy and finding.details["Sharz"] == p.Sharz
    assert np.isclose(finding.details["first_moment_at_axis"], first_moment, rtol=1e-5)
    assert any(name in r.getMessage() and "neutral axis" in r.getMessage() for r in ada_warnings)


@pytest.mark.parametrize("name", ["S01_IPE300", "S02_UNSI", "S03_TEE", "S07_ANG", "S08_ANGEQ", "S12_IEQ"])
def test_neutral_axis_in_the_web_is_not_reported(genie_sections, name, ada_warnings):
    with conversion_report.collect() as report:
        calculate_general_properties(genie_sections[name])
    assert not report.findings and not ada_warnings


@pytest.fixture(scope="module")
def edge_sections() -> dict[str, ada.Section]:
    a = ada.from_fem(EDGE_FEM)
    return {s.name: s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections}


def test_reference_files_are_genie_output():
    for path in (GENIE_FEM, EDGE_FEM):
        head = path.read_text().splitlines()[3]
        assert "SESAM GeniE" in head and "V8.13-02" in head
