"""Materials read from a Sesam SIF should pick the right ada model.

MISOSEL is generic linear-elastic-isotropic — it doesn't tell us
"steel". The reader previously promoted every MISOSEL to CarbonSteel,
and a non-canonical sig_y (anything other than 355 / 420 MPa) tripped
``CarbonSteel.GRADES["NA"]`` deep inside the constructor with
``KeyError: 'NA'``. The fix: emit a plain Metal for non-matching
sig_y so we don't fabricate a steel grade we don't know is correct.

These tests pin the helper-level dispatch so that fix doesn't quietly
regress the day someone re-routes the materials path.
"""

from __future__ import annotations

import pytest

from ada.fem.formats.sesam.results.read_sif import _build_metal, get_grade
from ada.materials.metals import CarbonSteel, Metal


def _prop_map(sig_y: float, *, sig_u: float | None = None) -> dict:
    """Build a MISOSEL-shaped prop_map. Keys mirror what the SIF
    reader produces from ``cards.MAT_MAP["MISOSEL"]``."""
    pm = {
        "E": 2.1e11,
        "v": 0.3,
        "rho": 7850.0,
        "zeta": 0.03,
        "alpha": 1.2e-5,
        "sig_y": sig_y,
    }
    if sig_u is not None:
        pm["sig_u"] = sig_u
    return pm


def test_get_grade_canonical_steels():
    assert get_grade(355e6) == CarbonSteel.TYPES.S355
    assert get_grade(420e6) == CarbonSteel.TYPES.S420


def test_get_grade_returns_na_for_unknown_yields():
    # 275 MPa (S275), 460 MPa (S460), 690 MPa (S690), 240 MPa (Al
    # 6061-T6) — none are in CarbonSteel.GRADES.
    for sig_y in (275e6, 460e6, 690e6, 240e6):
        assert get_grade(sig_y) == "NA"


def test_build_metal_returns_plain_metal_with_supplied_values():
    pm = _prop_map(275e6)
    m = _build_metal(pm)
    assert isinstance(m, Metal)
    assert not isinstance(m, CarbonSteel)
    assert m.E == pytest.approx(2.1e11)
    assert m.v == pytest.approx(0.3)
    assert m.rho == pytest.approx(7850.0)
    assert m.sig_y == pytest.approx(275e6)
    # sig_u defaults to sig_y when not on the card (MISOSEL doesn't
    # carry an ultimate-stress field).
    assert m.sig_u == pytest.approx(275e6)


def test_build_metal_for_orthotropic_without_sig_y_uses_zero_sentinel():
    # MORSMEL prop_map shape: no sig_y / sig_u keys at all.
    pm = {"E": 1.1e10, "v": 0.4, "rho": 600.0, "zeta": 0.0, "alpha": 5e-6}
    m = _build_metal(pm)
    assert isinstance(m, Metal)
    assert m.sig_y == 0.0
    assert m.sig_u == 0.0


def test_carbon_steel_still_works_for_canonical_grades():
    # Sanity: the new dispatch must not regress the canonical-grade
    # path the existing tests rely on.
    cs = CarbonSteel(grade="S355")
    assert cs.sig_y == pytest.approx(355e6)
    assert cs.grade == "S355"
