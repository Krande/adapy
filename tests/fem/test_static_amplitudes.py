"""A load amplitude in a static step, against statics, in Code_Aster.

A cantilever IPE300 along x, clamped at its root, with a tip force F along z under an amplitude over the step time
``T``: ``a(0) = 0``, ``a(T / 2) = 1``, ``a(T) = 0.4``. Abaqus solves a static step at its end, so the step's answer is
the force at ``a(T)``; over the step the root holds back ``F a(t)`` at every instant, exact for any beam.

The Code_Aster writer applied every load in full (the amplitude reported omitted): ``F`` instead of ``0.4 F``.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, StepImplicitStatic
from ada.fem.common import Amplitude
from ada.materials.metals import CarbonSteel

L, F, T = 3.0, 1.0e4, 2.0
AMP = ([0.0, T / 2, T], [0.0, 1.0, 0.4])


def _model():
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    tip = fem.add_set(FemSet("tip", [n for n in fem.nodes if abs(n.x - L) < 1e-9], "nset"))
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    amp = fem.add_amplitude(Amplitude("a", x=AMP[0], y=AMP[1]))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("amp", total_time=T, init_incr=T / 10, max_incr=T / 10))
    step.add_load(LoadPoint("f", F, tip, 3, amplitude=amp))
    return a, root


def test_the_static_step_ends_at_the_amplitudes_end_value(tmp_path, require_solver):
    require_solver("code_aster")
    a, root = _model()
    res = a.to_fem("amp", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    by_t = {}
    for r in res.results:
        if r.name.split("[")[0] == "REAC_NODA":
            (row,) = [row for row in np.asarray(r.values, float) if int(row[0]) == int(root.id)]
            by_t[float(r.step)] = -row[3]
    assert by_t, [r.name for r in res.results][:10]
    # instants 0 .. 1 stand for the step time 0 .. T
    for t, rz in by_t.items():
        assert rz == pytest.approx(F * np.interp(t * T, *AMP), abs=1e-6 * F), t
    assert by_t[max(by_t)] == pytest.approx(0.4 * F, rel=1e-9)
