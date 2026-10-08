from __future__ import annotations

import numpy as np

from ada.fem.formats.sesam.results.derived_fields import _surface_values_and_positions
from ada.fem.formats.sesam.results.derived_values import (
    beam_stress,
    decompose_shell,
    general_stress,
    membrane_principal,
    opposite_section_modulus,
    plane_principal,
    stress_resultants,
)
from ada.fem.formats.sesam.results.result_units import (
    common_result_unit,
    result_component_units,
)


def test_shell_surfaces_are_packed_top_first_with_layer_metadata():
    bottom = np.array([[[-10.0], [-20.0], [-30.0]]])
    top = np.array([[[10.0], [20.0], [30.0]]])

    values, positions = _surface_values_and_positions(bottom, top)

    assert values.shape == (1, 6, 1)
    assert values[0, :, 0].tolist() == [10.0, 20.0, 30.0, -10.0, -20.0, -30.0]
    assert [position[2] for position in positions] == [0.5, 0.5, 0.5, -0.5, -0.5, -0.5]
    assert [position[1] for position in positions] == ["1", "2", "3", "1", "2", "3"]


def test_shell_derived_values_match_reference_row():
    # Validation deck, case girder_local, element 3, result point 1.
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


def test_beam_stress_and_opposite_modulus_match_reference_row():
    # Validation deck, section 3 (L profile), case girder_local, element-average 1.
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


def test_beam_stress_per_element_matches_one_element_at_a_time():
    from ada.fem.formats.sesam.results.derived_values import (
        BEAM_STRESS_DENOMINATORS,
        beam_stress_per_element,
    )

    rng = np.random.default_rng(7)
    forces = rng.normal(size=(5, 3, 6)) * 1e3
    den = rng.uniform(1e-6, 1e-2, size=(5, len(BEAM_STRESS_DENOMINATORS)))
    den[1, 0] = 0.0  # no area: NaN, as beam_stress gives
    den[2, 6] = np.nan  # no opposite-side modulus
    den[3] = np.nan  # no section at all
    got = beam_stress_per_element(forces, den)
    assert got.shape == (5, 3, 8)
    for i in range(5):
        want = beam_stress(forces[i], **dict(zip(BEAM_STRESS_DENOMINATORS, den[i])))
        assert np.array_equal(got[i], want, equal_nan=True), i
    assert np.isnan(got[1]).all() and np.isnan(got[2]).all() and np.isnan(got[3]).all()
    # One element-average point per element: (n, 1, 6) works the same.
    avg = forces[:, :1, :]
    assert np.array_equal(
        beam_stress_per_element(avg, den)[0], beam_stress(avg[0], **dict(zip(BEAM_STRESS_DENOMINATORS, den[0])))
    )


def _nodal_shell_average_one_node_at_a_time(rows_by_node, node_ids):
    """The per-node formulation of the nodal shell average, as a reference."""
    bottom = np.full((len(node_ids), 3), np.nan, dtype=np.float32)
    top = np.full((len(node_ids), 3), np.nan, dtype=np.float32)
    thickness = np.full(len(node_ids), np.nan)
    cos_limit = np.cos(np.deg2rad(5.0))
    for ni, node_id in enumerate(node_ids):
        rows = rows_by_node.get(int(node_id), ())
        if len(rows) < 2:
            continue
        ref_t, ref_n = rows[0][2], rows[0][3]
        eligible = [
            row
            for row in rows
            if np.isfinite(row[2])
            and np.isfinite(ref_t)
            and abs(row[2] - ref_t) <= 0.1 * max(abs(ref_t), 1e-30)
            and np.all(np.isfinite(row[3]))
            and np.all(np.isfinite(ref_n))
            and float(np.dot(row[3], ref_n)) >= cos_limit
        ]
        if len(eligible) != len(rows) or len(eligible) < 2:
            continue
        bottom[ni] = np.mean([row[0] for row in eligible], axis=0)
        top[ni] = np.mean([row[1] for row in eligible], axis=0)
        thickness[ni] = float(np.mean([row[2] for row in eligible]))
    return bottom, top, thickness


def test_nodal_shell_average_matches_the_per_node_formulation():
    from collections import defaultdict

    from ada.fem.formats.sesam.results.derived_fields import _ShellNodalContributions

    rng = np.random.default_rng(3)
    up = np.array([0.0, 0.0, 1.0])
    contributions = _ShellNodalContributions()
    rows_by_node = defaultdict(list)
    # Two raw fields' worth of 4-node shells over nodes 1..30; node 30 is shared by
    # eighteen elements (np.mean's pairwise range), node 7 sees a thicker plate,
    # node 9 a tilted one, node 11 an element without a normal.
    for _ in range(2):
        n = 12
        refs = rng.integers(1, 30, size=(n, 4))
        refs[3:, 0] = 30
        thickness = np.full(n, 0.02)
        normals = np.tile(up, (n, 1))
        refs[0, 1], thickness[0] = 7, 0.05
        refs[1, 1], normals[1] = 9, [0.0, 0.5, 0.866]
        refs[2, 1], normals[2] = 11, np.nan
        bottom = rng.normal(size=(n, 4, 3)).astype(np.float32) * 1e6
        top = rng.normal(size=(n, 4, 3)).astype(np.float32) * 1e6
        contributions.add(refs, bottom, top, thickness, normals)
        for e in range(n):
            for c in range(4):
                rows_by_node[int(refs[e, c])].append((bottom[e, c], top[e, c], float(thickness[e]), normals[e]))

    node_ids = np.array([30, 3, 7, 9, 11, 99, *range(1, 30)])
    got = contributions.average(node_ids)
    want = _nodal_shell_average_one_node_at_a_time(rows_by_node, node_ids)
    for g, w in zip(got, want):
        assert g.dtype == w.dtype
        assert np.array_equal(g, w, equal_nan=True)
    assert np.isfinite(got[2][0])  # node 30, eighteen contributors
    assert np.isnan(got[2][5])  # node 99, none


def test_component_units_preserve_mixed_dimensions():
    si = (1.0, 1.0, 1.0)

    assert result_component_units(si, "G-STRESS", ("SIGXX", "VONMISES")) == ("Pa", "Pa")
    assert result_component_units(si, "DISPLACEMENT", ("ALL", "X", "RX")) == ("m", "m", "rad")
    assert result_component_units(si, "G-FORCE", ("NXX", "MXX")) == ("N", "N·m")
    assert result_component_units(si, "R-STRESS", ("NXX", "MXX")) == ("N/m", "N")
    assert common_result_unit(("Pa", "Pa")) == "Pa"
    assert common_result_unit(("N", "N·m")) == ""


def test_component_units_support_documented_mm_kn_set():
    units = result_component_units((0.001, 1000.0, 1.0), "REACTION-FORCE", ("X-FORCE", "RX-MOMENT"))

    assert units == ("kN", "kN·mm")
