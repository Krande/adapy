"""Free vibration (``StepEigen``) against Euler-Bernoulli's cantilever, in Code_Aster and Sestra (where installed).

A 20 m IPE300 cantilever along x, clamped at x = 0: its bending frequencies are ``f_n = (b_n L)^2 / (2 pi L^2)
sqrt(E I / (rho A))`` with ``b_1 L = 1.8751``, ``b_2 L = 4.6941``, in each plane (weak axis ``Iz``, strong ``Iy``).
Slender, so shear and rotary inertia -- which Sestra's beam has and Code_Aster's POU_D_E does not -- move them by at
most ~6e-4 (the strong axis, L / r_y = 160), under the 2e-3 asked; at 10 m Sestra's strong-axis mode was 2.6e-3 low.
Each expected frequency is matched to the nearest mode found: the twisting modes interleave and are not checked here.

On Sesam this is also the check that a SIN read gives the frequencies at all: its modal masses (RDMLFACT) used to be
attached to an empty summary, which then hid every mode (``read_sif.Sif2Mesh._add_modal_mass``).

On Sestra this checks the EIGA line on its fixed columns (write_steps.eiga_line): ENR 6-10, SHIFT 71-80.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, StepEigen
from ada.materials.metals import CarbonSteel

L = 20.0
BETA_L = (1.875104069, 4.694091133)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_cantilever_vibrates_at_euler_bernoullis_frequencies(tmp_path, require_solver, solver):
    require_solver(solver)
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=8))
    res = a.to_fem("eig", solver, scratch_dir=tmp_path, overwrite=True, execute=True)
    found = np.array(sorted(m.f_hz for m in res.get_eig_summary().modes if m.f_hz is not None), dtype=float)
    assert len(found) >= 6, found

    props, e, rho = bm.section.properties, mat.model.E, mat.model.rho

    def f(beta_l, inertia):
        return beta_l**2 / (2 * np.pi * L**2) * np.sqrt(e * inertia / (rho * props.Ax))

    weak, strong = min(props.Iy, props.Iz), max(props.Iy, props.Iz)
    want = {"weak 1": f(BETA_L[0], weak), "weak 2": f(BETA_L[1], weak), "strong 1": f(BETA_L[0], strong)}
    assert found[0] == pytest.approx(want["weak 1"], rel=2e-3), found
    for name, v in want.items():
        nearest = found[np.argmin(np.abs(found / v - 1))]
        assert nearest == pytest.approx(v, rel=2e-3), (name, v, found)
