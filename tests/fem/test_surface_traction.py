"""A surface traction by components (``LoadSurfaceTraction``, GeniE's ``component2d_constant``) against closed forms,
in Code_Aster, Sestra and CalculiX.

A shell strip ``L x b x t`` along x:

* **in its plane**, a traction ``fx`` (force per area) along x, the strip held along x at its root edge (and in y at
  one root node), every node held out of plane: the stress is ``sigma_xx = fx (L - x) / t`` -- an exact plane-stress
  field (no sigma_yy or shear, the lateral strain linear in x) -- so the root reacts ``-fx (L - H) b`` exactly and the tip
  moves ``fx L^2 / (2 E t)`` -- here ``fx (L^2 - H^2) / (2 E t)``, the root's row of elements (size ``H``) left
  unloaded so that no load sits on a supported node (with ``nu = 0``: a lateral contraction varying along x would be held back at the root);
* **across it**, a traction ``fz`` on the cantilevered strip: the root reacts ``-fz (L - H) b``, exact.

Sesam writes it as BEUSLO LOTYP 2, GeniE's own form (matched against its T1.FEM in
``tests/core/fem/test_concept_loads_to_fem.py``, LC_comp); Code_Aster as FORCE_COQUE FX/FY/FZ; CalculiX as the consistent nodal forces of the faces (*CLOAD,
``ada.fem.face_loads``), its results on the bricks it expands the shells into -- so the nodes are picked by the result
mesh's own positions.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadSurfaceTraction, StepImplicitStatic
from ada.fem.outputs import FieldOutput
from ada.materials.metals import CarbonSteel

L, B, T = 2.0, 0.5, 0.01


def _strip(traction, in_plane: bool):
    # nu = 0: with lateral contraction the exact field has a u_x term -nu fx y^2 / (2 E t), which a root held at
    # u_x = 0 along its whole edge contradicts (0.25 % on the tip here); without it the bar's closed form is exact
    mat = ada.Material("S355", CarbonSteel("S355", v=0.0))
    pl = ada.Plate("strip", [(0, 0), (L, 0), (L, B), (0, B)], T, mat=mat)
    p = ada.Part("P") / pl
    p.fem = p.to_fem_obj(H, use_quads=True)
    fem = p.fem
    root = [n for n in fem.nodes if abs(n.x) < 1e-9]
    if in_plane:
        fem.add_bc(Bc("root_x", fem.add_set(FemSet("root", root, "nset")), [1]))
        (r0,) = [n for n in root if abs(n.y) < 1e-9]
        fem.add_bc(Bc("root_y", fem.add_set(FemSet("r0", [r0], "nset")), [2]))
        fem.add_bc(Bc("flat", fem.add_set(FemSet("all", list(fem.nodes), "nset")), [3, 4, 5, 6]))
    else:
        fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", root, "nset")), [1, 2, 3, 4, 5, 6]))
    # every element but the root's row: ccx's RF is the external force at a node, the reaction plus any load applied
    # there (results.structural), so the root nodes carry none and RF is the reaction in every solver
    loaded = [el for el in fem.elements.shell if all(n.x > 1e-9 for n in el.nodes)]
    els = fem.add_set(FemSet("loaded", loaded, "elset"))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("pull"))
    step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))  # CalculiX writes only what is asked for
    step.add_load(LoadSurfaceTraction("t", traction, els))
    tip = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    return a, root, tip, mat.model.E


def _at_x(res, x0):
    m = res.mesh.nodes
    return [int(i) for i, c in zip(m.identifiers, np.asarray(m.coords, float)) if abs(c[0] - x0) < 1e-9]


#: The element size of the strip's mesh.
H = 0.125


def _fields(res):
    ((_, fields),) = res.case_fields("nodal_displacement", "nodal_reaction").items()
    out = []
    for role in ("nodal_displacement", "nodal_reaction"):
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == role]
        out.append({int(r[0]): np.asarray(r[1:4], float) for r in np.asarray(f.values, float)})
    return out


@pytest.mark.parametrize("solver", ["code_aster", "sesam", "calculix"])
def test_an_in_plane_traction_stretches_the_strip_as_a_bar_under_its_own_weight(tmp_path, require_solver, solver):
    require_solver(solver)
    fx = 1.0e6
    a, _, _, e = _strip((fx, 0.0, 0.0), in_plane=True)
    res = a.to_fem("tx", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    u, reac = _fields(res)
    rel = 1e-9 if solver == "code_aster" else 1e-5
    assert sum(reac[i][0] for i in _at_x(res, 0.0)) == pytest.approx(-fx * (L - H) * B, rel=rel)
    tip = _at_x(res, L)
    assert tip
    for i in tip:
        # sigma = fx (L - x) / t beyond the root's row, fx (L - H) / t within it: u = fx (L^2 - H^2) / (2 E t)
        assert u[i][0] == pytest.approx(fx * (L**2 - H**2) / (2 * e * T), rel=1e-6 if solver != "calculix" else 1e-5)


@pytest.mark.parametrize("solver", ["code_aster", "sesam", "calculix"])
def test_a_traction_across_the_strip_is_held_back_by_its_root(tmp_path, require_solver, solver):
    require_solver(solver)
    fz = -1000.0
    a, _, _, _ = _strip((100.0, 0.0, fz), in_plane=False)
    res = a.to_fem("tz", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    _, reac = _fields(res)
    rel = 1e-9 if solver == "code_aster" else 1e-5
    root = _at_x(res, 0.0)
    assert sum(reac[i][2] for i in root) == pytest.approx(-fz * (L - H) * B, rel=rel)
    # the root carries the bending moment as a couple of x forces across the thickness (the bricks ccx expands the
    # shell into, M / t ~ 1e5 N here), which the .frd prints to six digits: their sum is good to 1e-5 of their size
    size = sum(abs(reac[i][0]) for i in root) if solver == "calculix" else 0.0
    assert sum(reac[i][0] for i in root) == pytest.approx(-100.0 * (L - H) * B, rel=rel, abs=1e-5 * size)
