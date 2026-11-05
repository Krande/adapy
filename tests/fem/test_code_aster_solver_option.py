"""``options.CODE_ASTER.solver``: the static steps' linear solver.

A model with a near-rigid part -- equipment supports modelled as beams of E 1000 times steel's, say -- is
ill-conditioned enough that MUMPS's error estimate passes its default limit (1e-6) and Code_Aster stops the run at
<FACTOR_57>, while the answer is fine: measured, Code_Aster 18.1.11, a 22 000-dof model of shells and beams with such
supports stopped at an estimate of 2.5e-6, and MULT_FRONT and MUMPS at RESI_RELA=1e-4 gave the same displacements to 1e-11 m (max 1.7 mm).
There was no way to choose the solver but editing the written deck.

Here a 4 m steel cantilever tube carries a 2 m tube of E 1000 times steel's on its tip, a 10 kN tip load: the answer
is the steel part's ``F (L + l)`` moment and shear, so every solver gives the same tip displacement.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, StepImplicitStatic
from ada.materials.metals import CarbonSteel


def _model(solver=None, resi_rela=None) -> ada.Assembly:
    steel = ada.Material("S355", CarbonSteel("S355"))
    rigid = ada.Material("rigid", CarbonSteel("S355", E=2.1e14))
    soft = ada.Beam("soft", (0, 0, 0), (4, 0, 0), "TUB200x10", steel)
    stiff = ada.Beam("stiff", (4, 0, 0), (6, 0, 0), "TUB200x10", rigid)
    p = ada.Part("P") / [soft, stiff]
    p.fem = p.to_fem_obj(0.25, "line")
    root = [n for n in p.fem.nodes if abs(n.x) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - 6) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("root", root, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    tip_set = p.fem.add_set(FemSet("tip", tip, FemSet.TYPES.NSET))
    p.fem.options.CODE_ASTER.solver = solver
    p.fem.options.CODE_ASTER.resi_rela = resi_rela
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("tip")).add_load(ada.fem.LoadPoint("f", -1.0e4, tip_set, 3))
    return a


def _comm(tmp_path, **kw) -> str:
    _model(**kw).to_fem("so", "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / "so" / "so.comm").read_text()


def test_the_solver_option_is_written(tmp_path):
    assert "SOLVEUR" not in _comm(tmp_path / "default")
    assert "SOLVEUR=_F(METHODE='MULT_FRONT')," in _comm(tmp_path / "mf", solver="MULT_FRONT")
    assert "SOLVEUR=_F(METHODE='MUMPS', RESI_RELA=0.0001)," in _comm(tmp_path / "mu", solver="mumps", resi_rela=1e-4)


def test_an_unknown_solver_is_refused(tmp_path):
    from ada.fem.exceptions.element_support import IncompatibleElements

    with pytest.raises(IncompatibleElements, match="PETSC"):
        _comm(tmp_path, solver="PETSC")


def test_every_solver_gives_the_same_answer(tmp_path, require_solver):
    require_solver("code_aster")
    tips = {}
    for solver in ("MULT_FRONT", "LDLT", "MUMPS"):
        res = _model(solver=solver, resi_rela=1e-4 if solver == "MUMPS" else None).to_fem(
            f"so_{solver}", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True
        )
        disp = next(np.asarray(r.values, float) for r in res.results if "DEPL" in r.name)
        tips[solver] = np.nanmax(np.abs(disp[:, 3]))
    assert tips["MULT_FRONT"] > 0
    assert tips["LDLT"] == pytest.approx(tips["MULT_FRONT"], rel=1e-7)
    assert tips["MUMPS"] == pytest.approx(tips["MULT_FRONT"], rel=1e-7)
