"""A surface traction by components (``LoadSurfaceTraction``, GeniE's ``component2d_constant``) against closed forms,
in Code_Aster and Sestra.

A shell strip ``L x b x t`` along x:

* **in its plane**, a traction ``fx`` (force per area) along x, the strip held along x at its root edge (and in y at
  one root node), every node held out of plane: the stress is ``sigma_xx = fx (L - x) / t`` -- an exact plane-stress
  field (no sigma_yy or shear, the lateral strain linear in x) -- so the root reacts ``-fx L b`` exactly and the tip
  moves ``fx L^2 / (2 E t)`` (with ``nu = 0``: a lateral contraction varying along x would be held back at the root);
* **across it**, a traction ``fz`` on the cantilevered strip: the root reacts ``-fz L b``, exact.

Sesam writes it as BEUSLO LOTYP 2, GeniE's own form (matched against its T1.FEM in
``tests/core/fem/test_concept_loads_to_fem.py``, LC_comp); Code_Aster as FORCE_COQUE FX/FY/FZ.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadSurfaceTraction, StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, B, T = 2.0, 0.5, 0.01


def _strip(traction, in_plane: bool):
    # nu = 0: with lateral contraction the exact field has a u_x term -nu fx y^2 / (2 E t), which a root held at
    # u_x = 0 along its whole edge contradicts (0.25 % on the tip here); without it the bar's closed form is exact
    mat = ada.Material("S355", CarbonSteel("S355", v=0.0))
    pl = ada.Plate("strip", [(0, 0), (L, 0), (L, B), (0, B)], T, mat=mat)
    p = ada.Part("P") / pl
    p.fem = p.to_fem_obj(0.125, use_quads=True)
    fem = p.fem
    root = [n for n in fem.nodes if abs(n.x) < 1e-9]
    if in_plane:
        fem.add_bc(Bc("root_x", fem.add_set(FemSet("root", root, "nset")), [1]))
        (r0,) = [n for n in root if abs(n.y) < 1e-9]
        fem.add_bc(Bc("root_y", fem.add_set(FemSet("r0", [r0], "nset")), [2]))
        fem.add_bc(Bc("flat", fem.add_set(FemSet("all", list(fem.nodes), "nset")), [3, 4, 5, 6]))
    else:
        fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", root, "nset")), [1, 2, 3, 4, 5, 6]))
    els = fem.add_set(FemSet("loaded", list(fem.elements.shell), "elset"))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("pull"))
    step.add_load(LoadSurfaceTraction("t", traction, els))
    tip = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    return a, root, tip, mat.model.E


def _fields(res):
    ((_, fields),) = res.case_fields("nodal_displacement", "nodal_reaction").items()
    out = []
    for role in ("nodal_displacement", "nodal_reaction"):
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == role]
        out.append({int(r[0]): np.asarray(r[1:4], float) for r in np.asarray(f.values, float)})
    return out


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_an_in_plane_traction_stretches_the_strip_as_a_bar_under_its_own_weight(tmp_path, require_solver, solver):
    require_solver(solver)
    fx = 1.0e6
    a, root, tip, e = _strip((fx, 0.0, 0.0), in_plane=True)
    u, reac = _fields(a.to_fem("tx", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-9
    assert sum(reac[int(n.id)][0] for n in root) == pytest.approx(-fx * L * B, rel=rel)
    for n in tip:
        assert u[int(n.id)][0] == pytest.approx(fx * L**2 / (2 * e * T), rel=1e-6)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_traction_across_the_strip_is_held_back_by_its_root(tmp_path, require_solver, solver):
    require_solver(solver)
    fz = -1000.0
    a, root, _, _ = _strip((100.0, 0.0, fz), in_plane=False)
    _, reac = _fields(a.to_fem("tz", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-9
    assert sum(reac[int(n.id)][2] for n in root) == pytest.approx(-fz * L * B, rel=rel)
    assert sum(reac[int(n.id)][0] for n in root) == pytest.approx(-100.0 * L * B, rel=rel)
