"""TPROFILE section properties: a T is a T, whatever adapy keeps in its bottom-flange slots.

adapy stores a T in the I-section fields. ``string_to_section``, ``from_geometry`` and the IFC
reader fill the absent bottom flange as a stub as wide as the web (``w_btn = t_w``,
``t_fbtn = t_ftop``); the gxml reader keeps whatever GeniE's unsymmetrical_i_section had there;
``geom_beams`` (an IFC ``TShapeProfileDef``) leaves both ``None``.
"""

from __future__ import annotations

import numpy as np

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
