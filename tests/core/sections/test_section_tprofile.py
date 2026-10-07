"""TPROFILE section properties: a T is a T, whatever adapy keeps in its bottom-flange slots.

adapy stores a T in the I-section fields. ``string_to_section``, ``from_geometry`` and the IFC
reader fill the absent bottom flange as a stub as wide as the web (``w_btn = t_w``,
``t_fbtn = t_ftop``); the gxml reader keeps whatever GeniE's unsymmetrical_i_section had there;
``geom_beams`` (an IFC ``TShapeProfileDef``) leaves both ``None``.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.sections.properties import calculate_general_properties

# T 300 high, flange 200 x 15, web 10
H, B, TW, TF = 0.3, 0.2, 0.01, 0.015


def _t_closed_form():
    """Ax, Cz (from the web's foot) and Iy of the T as two rectangles."""
    hw = H - TF
    ax = B * TF + TW * hw
    cz = (B * TF * (H - TF / 2) + TW * hw**2 / 2) / ax
    iy = B * TF**3 / 12 + B * TF * (H - TF / 2 - cz) ** 2 + TW * hw**3 / 12 + TW * hw * (hw / 2 - cz) ** 2
    return ax, cz, iy


def test_a_tprofile_without_bottom_flange_slots_is_a_t():
    """With ``w_btn``/``t_fbtn`` left ``None`` (as ``geom_beams`` builds a T from an IFC
    ``TShapeProfileDef``) the I-section fallback copied the top flange to the bottom: Ax 8.70e-3,
    Cz 0.15 and Iy 1.3835e-4 -- a symmetric I. The T: Ax 5.85e-3, Cz 0.219423, Iy 5.22318e-5
    (GeniE V8.13-02, M1_T300 in genie_v8_13_review437_T1.FEM: 5.84999984e-3, 5.22318041e-5)."""
    tee = ada.Section("T", sec_type="TG", h=H, w_top=B, t_w=TW, t_ftop=TF)
    assert tee.type == tee.TYPES.TPROFILE and tee.w_btn is None and tee.t_fbtn is None
    ax, cz, iy = _t_closed_form()
    p = calculate_general_properties(tee)
    assert np.isclose(p.Ax, ax, rtol=1e-12)
    assert np.isclose(p.Cz, cz, rtol=1e-12)
    assert np.isclose(p.Iy, iy, rtol=1e-12)


# --- what a T is: GeniE's own T, and independent of the placeholder in the bottom slots ----------

REF_DIR = pathlib.Path(__file__).resolve().parents[3] / "files/fem_files/sesam/section_props"
REVIEW_FEM = REF_DIR / "genie_v8_13_review437_T1.FEM"
FIELDS = ("Ax", "Ix", "Iy", "Iz", "Wxmin", "Wymin", "Wzmin", "Shary", "Sharz", "Shceny", "Shcenz", "Sy", "Sz")
GENIE_STUB = 1e-6  # GeniE's library T: the absent flange 0.001 mm thick, 0.001 mm wider than the web

# GeniE section in genie_v8_13_review437_T1.FEM -> the same T as adapy builds it (stub as wide as the web)
GENIE_TEES = {
    "M1_T300": dict(h=0.3, w_top=0.2, t_w=0.01, t_ftop=0.015),
    "M1_TG650": dict(h=0.65, w_top=0.3, t_w=0.025, t_ftop=0.04),
    "M1_TEQ": dict(h=0.2, w_top=0.15, t_w=0.01, t_ftop=0.01),
    "M1_E1T": dict(h=0.1, w_top=0.3, t_w=0.005, t_ftop=0.05),
}


def _tee(t_fbtn="flange", w_btn="web", **dims):
    w_btn = dims["t_w"] if w_btn == "web" else w_btn
    t_fbtn = dims["t_ftop"] if t_fbtn == "flange" else t_fbtn
    return ada.Section("T", sec_type="TG", w_btn=w_btn, t_fbtn=t_fbtn, **dims)


@pytest.fixture(scope="module")
def genie_tees() -> dict[str, ada.Section]:
    a = ada.from_fem(REVIEW_FEM)
    return {s.name: s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections}


@pytest.mark.parametrize("name", list(GENIE_TEES))
def test_a_tprofile_is_genies_own_t(genie_tees, name):
    """GeniE has no T section. Its T library (Libraries/tbar.xml, all 202 entries) writes a T as an
    unsymmetrical I whose absent flange is 0.001 mm thick and 0.001 mm wider than the web; the four
    Ts here are built that way in genie_v8_13_review437.js. adapy's TPROFILE -- stored with a stub
    as wide as the web and as thick as the flange -- equals that T.

    Before: the stub counted as a second flange. T 300x200x10x15: Shary 3.82768e-3 for GeniE's
    1.91397e-3 (x2.000 on all four), Ix +1.95 % (+3.39 % TG650, -25.8 % for TEQ, whose equal
    thicknesses took the equal-thickness I formula), Shcenz -4.9e-4 to -1.9e-3 relative.

    Tolerance, measured: GeniE's 0.001 mm flange adds delta / t_ftop to SHARY (1e-4 for TEQ),
    which is divided out; its web, shorter by delta, moves Ix and Wxmin by up to 2.95e-6 (TEQ);
    everything else agrees to 1.3e-7. Hence 1e-5, 28 times tighter than the smallest change made
    (Shcenz of the E1 T, 2.8e-4).

    GeniE's T Ix is the I-section formula 1.3 sum(b t^3) / 3; a warping-function FE solution
    (sectionproperties 3.10.2) gives J 3.14537e-7 for T300 and 1.13263e-7 for TEQ, so GeniE (and
    now adapy) is 32 % and 30 % above it. For TEQ adapy was -3.5 % (1.09333e-7) before, by way of
    the equal-thickness I formula the 15 mm stub happened to trigger."""
    dims = GENIE_TEES[name]
    calc, genie = calculate_general_properties(_tee(**dims)), genie_tees[name].properties
    assert genie_tees[name].t_fbtn == pytest.approx(GENIE_STUB, rel=1e-6)
    bad = []
    for field in FIELDS:
        c, r = getattr(calc, field), getattr(genie, field)
        if field == "Shary":
            r *= dims["t_ftop"] / (dims["t_ftop"] + GENIE_STUB)
        if not np.isclose(c, r, rtol=1e-5, atol=1e-12):
            bad.append(f"{field}: adapy {c:.8e} GeniE {r:.8e}")
    assert not bad, "\n".join(bad)


def test_the_y_shear_area_of_a_t_is_its_flanges():
    """Thin-walled, the cut y = 0 crosses the flange once: Shary = Iz t_ftop / Sz = 1.91384e-3 for
    T 300x200x10x15 (flange area 3.0e-3; energy-consistent, sectionproperties 3.10.2: 2.5387e-3).
    The stub made it Iz (2 t_ftop) / Sz = 3.82768e-3."""
    p = calculate_general_properties(_tee(**GENIE_TEES["M1_T300"]))
    assert np.isclose(p.Shary, p.Iz * 0.015 / p.Sz, rtol=1e-12)
    assert np.isclose(p.Shary, 1.91384e-3, rtol=1e-5)


ENCODINGS = {
    "stub as wide as the web, as thick as the flange (string_to_section, from_geometry, IFC)": {},
    "no bottom slots (geom_beams from an IFC TShapeProfileDef)": dict(w_btn=None, t_fbtn=None),
    "GeniE library T, 0.001 mm (gxml reader)": dict(w_btn=0.010001, t_fbtn=1e-6),
    "manual GeniE T, 0.1 mm as wide as the web (gxml reader)": dict(t_fbtn=1e-4),
}


@pytest.mark.parametrize("encoding", list(ENCODINGS), ids=["stub", "none", "genie-library", "manual-0.1mm"])
def test_the_bottom_slots_do_not_change_a_t(encoding, ada_warnings):
    """Every placeholder adapy puts in a T's bottom-flange slots gives the same section, and none of
    them is warned about. Before, Shary was 3.82768e-3 / 3.82768e-3 (a symmetric I's 3.91638e-3 before
    the previous commit) / 1.91397e-3 / 1.92660e-3 for the four, and Ix 4.24125e-7 twice, then 4.16000e-7
    and 4.15957e-7; now 1.91384e-3 and 4.16000e-7 for all."""
    ref = calculate_general_properties(_tee(**GENIE_TEES["M1_T300"]))
    p = calculate_general_properties(_tee(**ENCODINGS[encoding], **GENIE_TEES["M1_T300"]))
    for field in FIELDS:
        assert getattr(p, field) == pytest.approx(getattr(ref, field), rel=1e-12, abs=1e-15), field
    assert not ada_warnings


def test_a_tprofile_with_a_real_bottom_flange_is_named(ada_warnings):
    """A bottom flange that carries area past the web is not part of a T and is left out -- said,
    with the section's name, rather than silently -- and the section is the T."""
    p = calculate_general_properties(_tee(w_btn=0.1, t_fbtn=0.01, **GENIE_TEES["M1_T300"]))
    (record,) = [r for r in ada_warnings if "TPROFILE" in r.getMessage()]
    assert '"T"' in record.getMessage() and "I-profile" in record.getMessage()
    ref = calculate_general_properties(_tee(**GENIE_TEES["M1_T300"]))
    for field in FIELDS + ("Cy", "Cz"):
        assert getattr(p, field) == pytest.approx(getattr(ref, field), rel=1e-12, abs=1e-15), field
