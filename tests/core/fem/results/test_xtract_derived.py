from __future__ import annotations

import numpy as np

from ada.fem.formats.sesam.results.xtract_derived import (
    beam_stress,
    decompose_shell,
    general_stress,
    membrane_principal,
    opposite_section_modulus,
    plane_principal,
    stress_resultants,
)


def test_shell_derived_values_match_xtract_reference_row():
    # mini_065, case girder_local, element 3, result point 1.
    bottom = np.array([-18501.4414, 34330.4570, -88173.4922])
    top = np.array([-10081.1406, 22917.3867, -89579.0547])
    d = decompose_shell(bottom, top)
    assert np.allclose(d[:6], [-14291.3, 28623.9, 4210.15, -5706.54, -88876.3, -702.781], rtol=5e-6)
    assert np.isclose(d[6], 158523.0, rtol=5e-6)

    r = stress_resultants(d, 0.01)
    assert np.allclose(
        r,
        [-142.913, -888.763, 286.239, -0.0117130, 0.0701692, -0.0951090],
        rtol=5e-5,
    )


def test_principal_and_von_mises_are_derived_after_averaging():
    basic = np.array([10.0, -2.0, 3.0])
    p = plane_principal(*basic)
    assert p[0] >= p[1]
    g = general_stress(basic)
    assert np.isclose(g[-1], np.sqrt(10**2 + (-2) ** 2 - 10 * -2 + 3 * 3**2))

    d = np.array([10.0, -2.0, 1.0, 2.0, 3.0, 4.0, 0.0])
    assert np.allclose(membrane_principal(d), p)


def test_beam_stress_and_opposite_modulus_match_xtract_reference_row():
    # mini_065 section 3 (L profile), case girder_local, element-average 1.
    forces = np.array([17.0405, 0.33257, -0.286778, 0.0412886, -2.1367, 0.159368])
    iy = 1.4367315998242702e-05
    iz = 3.217272421807138e-07
    wy = 0.0001060865179169923
    wz = 1.0496204595256131e-05
    wy2 = opposite_section_modulus(iy, wy, 0.22)
    wz2 = opposite_section_modulus(iz, wz, 0.041)
    b = beam_stress(
        forces,
        area=0.0029765500221401453,
        wxmin=8.231653737311717e-06,
        wymin=wy,
        wzmin=wz,
        shary=0.0006848677294328809,
        sharz=0.0015666598919779062,
        wymin2=wy2,
        wzmin2=wz2,
    )
    assert np.allclose(
        b,
        [5724.92, 20141.1, 15183.4, 5015.84, 485.597, -183.05, -12577.2, -5126.0],
        rtol=5e-5,
    )
