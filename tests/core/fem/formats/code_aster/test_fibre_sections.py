"""No solver: a beam section cut into fibres (``write_fibres.section_fibres``) holds its area and inertias."""

from __future__ import annotations

import pytest

import ada
from ada.fem.formats.code_aster.write.write_fibres import section_fibres


@pytest.mark.parametrize(
    "name", ["IPE300", "HEB200", "BG300x200x10x15", "FB200x20", "PIPE300x10", "TUB100x8", "L100x10"]
)
def test_the_fibres_hold_the_sections_area_and_inertias(name):
    sec = ada.Section("s", from_str=name)
    f = section_fibres(sec)
    p = sec.properties
    y, z, a = f[:, 0], f[:, 1], f[:, 2]
    assert a.sum() == pytest.approx(p.Ax, rel=1e-2)
    # about the centroid: no first moment
    assert abs((a * y).sum()) < 1e-9 * p.Ax and abs((a * z).sum()) < 1e-9 * p.Ax
    # Code_Aster's PREC_INERTIE is 10 %; the fibres hold far better than that
    assert (a * z**2).sum() == pytest.approx(p.Iy, rel=2e-2)
    assert (a * y**2).sum() == pytest.approx(p.Iz, rel=2e-2)
