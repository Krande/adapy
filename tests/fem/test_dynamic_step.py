"""An implicit dynamic step (``StepImplicitDynamic``) against a single degree of freedom's closed form, in Code_Aster.

A point mass ``m`` at the tip of a (practically) massless bar of axial stiffness ``k = E A / L``, clamped at the root,
the tip free along the bar only. A force ``F`` applied at t = 0 and held (a step load) makes it oscillate about the
static position:

``x(t) = (F / k) (1 - cos(w t))``, ``w = sqrt(k / m)``: twice the static deflection at half a period.

With an amplitude ramping the force linearly over ``t_r``, the response after the ramp is the static one plus an
oscillation of ``F / k * |sin(w t_r / 2)| / (w t_r / 2)`` (the ramp's dynamic factor); a ramp of exactly one period
leaves the mass at rest at the static deflection.

The Code_Aster writer wrote nothing for a dynamic step (an empty string: no deck command at all). Newmark's average
acceleration at ``T / 200`` keeps the period error under 1e-4.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, Mass, StepImplicitDynamic
from ada.fem.common import Amplitude
from ada.materials.metals import CarbonSteel

L, M, F = 4.0, 1000.0, 1.0e5


def _sdof(n_periods: float, ramp: float | None = None):
    mat = ada.Material("light", CarbonSteel("S355", rho=1e-6))
    bm = ada.Beam("bar", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(L, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    tip_set = fem.add_set(FemSet("tip", [tip], "nset"))
    fem.add_bc(Bc("axial_only", tip_set, [2, 3, 4, 5, 6]))
    fem.add_mass(Mass("m", tip_set, M))
    k = mat.model.E * bm.section.properties.Ax / L
    w = np.sqrt(k / M)
    period = 2 * np.pi / w
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitDynamic("shake", total_time=n_periods * period, init_incr=period / 200))
    amp = None
    if ramp is not None:
        amp = Amplitude("ramp", x=[0.0, ramp * period], y=[0.0, 1.0])
        fem.add_amplitude(amp)
    step.add_load(LoadPoint("push", F, tip_set, 1, amplitude=amp))
    return a, tip, k, w, period


def _tip_history(res, tip):
    out = []
    for r in res.results:
        if r.name.split("[")[0].endswith("DEPL"):
            v = {int(row[0]): row[1] for row in np.asarray(r.values, float)}
            out.append((float(r.step), v[int(tip.id)]))
    return sorted(out)


def test_a_suddenly_loaded_mass_swings_to_twice_the_static_deflection(tmp_path, require_solver):
    require_solver("code_aster")
    a, tip, k, w, period = _sdof(1.0)
    res = a.to_fem("sdof", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    hist = _tip_history(res, tip)
    assert len(hist) > 100
    for t, x in hist[:: len(hist) // 20]:
        assert x == pytest.approx(F / k * (1 - np.cos(w * t)), abs=2e-3 * F / k)
    assert max(x for _, x in hist) == pytest.approx(2 * F / k, rel=1e-3)


def test_a_force_ramped_over_one_period_leaves_the_mass_at_rest(tmp_path, require_solver):
    require_solver("code_aster")
    a, tip, k, w, period = _sdof(2.0, ramp=1.0)
    res = a.to_fem("ramp", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    after = [x for t, x in _tip_history(res, tip) if t > 1.05 * period]
    assert after and max(abs(x - F / k) for x in after) < 5e-3 * F / k
