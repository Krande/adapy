"""Rigid links in some dofs only (GeniE's default rigid link ties the translations, not the rotations), against a
simply supported beam's closed form, in Code_Aster.

A beam along x, both its end nodes the dependents of one rigid link whose master is held in all six dofs: linked in
the translations only, the ends cannot move but are free to rotate -- a simply supported beam (one end also held
against twisting). A midspan force P bends it by ``P L^3 / (48 E I)``, and the master carries P.

Code_Aster used to write every coupling as LIAISON_SOLIDE, all six dofs, whatever its dofs (a log line said so): the
ends came out clamped, ``P L^3 / (192 E I)``, a quarter of the answer. A partial coupling is now the rigid link's
kinematics in its own dofs, ``LIAISON_DDL`` per dependent node and dof.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, Constraint, FemSet, LoadPoint, StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, P = 4.0, 1.0e4


def _beam(dofs):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (a,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (b,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    (mid,) = [n for n in fem.nodes if abs(n.x - L / 2) < 1e-9]
    master = fem.nodes.add(ada.Node((L / 2, 0.0, 1.0)))
    m_set = fem.add_set(FemSet("master", [master], "nset"))
    s_set = fem.add_set(FemSet("ends", [a, b], "nset"))
    fem.add_constraint(Constraint("link", Constraint.TYPES.COUPLING, m_set, s_set, dofs=dofs))
    fem.add_bc(Bc("hold", m_set, [1, 2, 3, 4, 5, 6]))
    fem.add_bc(Bc("twist", fem.add_set(FemSet("a", [a], "nset")), [4]))
    if 1 not in dofs:
        fem.add_bc(Bc("axial", fem.add_set(FemSet("a_x", [a], "nset")), [1]))
    asm = ada.Assembly("A") / p
    step = asm.fem.add_step(StepImplicitStatic("bend"))
    step.add_load(LoadPoint("p", -P, fem.add_set(FemSet("mid", [mid], "nset")), 3))
    props, e = bm.section.properties, mat.model.E
    return asm, mid, master, P * L**3 / (48 * e * props.Iy)


@pytest.mark.parametrize("dofs", [[1, 2, 3], [2, 3]], ids=["translations", "dy_dz_only"])
def test_a_translation_link_makes_the_beam_simply_supported(tmp_path, require_solver, dofs):
    require_solver("code_aster")
    asm, mid, master, want = _beam(dofs)
    res = asm.to_fem("link", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    disp = next(np.asarray(r.values, float) for r in res.results if "DEPL" in r.name)
    reac = next(np.asarray(r.values, float) for r in res.results if "REAC_NODA" in r.name)
    (row,) = [r for r in disp if int(r[0]) == int(mid.id)]
    assert -row[3] == pytest.approx(want, rel=1e-3)
    # the link's reaction is where its equations act, on the ends; the whole of it is P
    assert np.nansum(reac[:, 3]) == pytest.approx(P, rel=1e-6)


def test_a_rigid_link_in_all_dofs_is_still_liaison_solide(tmp_path):
    asm, *_ = _beam([1, 2, 3, 4, 5, 6])
    asm.to_fem("solid", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "solid" / "solid.comm").read_text()
    assert "LIAISON_SOLIDE" in comm and "LIAISON_DDL" not in comm
