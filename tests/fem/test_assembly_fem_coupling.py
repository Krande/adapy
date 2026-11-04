"""A model whose FEM is the assembly's own (``a.fem = a.to_fem_obj(...)``), with a rigid link,
solved: the link is written once and the reactions balance the load, in Sestra and in Code_Aster.

Two 2 m tubes along x: ``A`` clamped at x = 0, ``B`` from x = 2.5 to 4.5, free; ``B``'s first node is tied to ``A``'s
tip by a rigid coupling. The model is accelerated 2 m/s^2 along z, so the base reaction is the whole mass times 2.

Measured before the fix (Sestra V11.3-01): the Sesam writer wrote its FEMs as ``[part.fem, assembly.fem]``, and with
the FEM on the assembly that is the same FEM twice, so every BLDEP record was written twice. Sestra adds the betas of
a repeated (slave, master) pair, so the slave moved twice as far as its master: the base reaction was 1.50 of the
load (454.6 N against 303.0 N) and Sestra's own listing showed load and reaction sums 151.5 N apart. In a
shell-and-beam model with four such links (16 slave nodes) the reactions were 1.43 of the load (291.4 kN against
203.5 kN) and the nodes above the links moved 3.1 mm instead of 0.97 mm.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, Constraint, FemSet, Load, StepImplicitStatic
from ada.materials.metals import CarbonSteel

ACC = 2.0


def _model() -> ada.Assembly:
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm_a = ada.Beam("A", (0, 0, 0), (2, 0, 0), "TUB100x8", mat)
    bm_b = ada.Beam("B", (2.5, 0, 0), (4.5, 0, 0), "TUB100x8", mat)
    a = ada.Assembly("Asm") / (ada.Part("P") / [bm_a, bm_b])
    a.fem = a.to_fem_obj(0.5, "line")
    fem = a.fem

    def at(x):
        return [n for n in fem.nodes if abs(n.x - x) < 1e-9 and abs(n.y) < 1e-9 and abs(n.z) < 1e-9]

    fem.add_bc(Bc("fix", fem.add_set(FemSet("base", at(0.0), FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    m = fem.add_set(FemSet("tip_a", at(2.0), FemSet.TYPES.NSET))
    s = fem.add_set(FemSet("start_b", at(2.5), FemSet.TYPES.NSET))
    fem.add_constraint(Constraint("link", Constraint.TYPES.COUPLING, m, s, dofs=[1, 2, 3, 4, 5, 6], parent=fem))
    step = fem.add_step(StepImplicitStatic("acc"))
    step.add_load(Load("acc_z", Load.TYPES.GRAVITY, ACC, dof=[0, 0, 1]))
    return a


def _weight(a: ada.Assembly) -> float:
    return sum(
        bm.material.model.rho * bm.section.properties.Ax * bm.length * ACC
        for bm in a.get_all_physical_objects(by_type=ada.Beam)
    )


def test_assembly_fem_coupling_written_once(tmp_path):
    a = _model()
    a.to_fem("cpl", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "cpl" / "cplT1.FEM").read_text()
    assert sum(1 for line in deck.splitlines() if line.startswith("BLDEP")) == 1


@pytest.mark.parametrize("fem_format", ["sesam", "code_aster"])
def test_assembly_fem_coupling_reactions_balance(tmp_path, require_solver, fem_format):
    require_solver(fem_format)
    a = _model()
    res = a.to_fem("cpl", fem_format, scratch_dir=tmp_path, overwrite=True, execute=True)
    if res is None:
        from ada.fem.formats.utils import default_fem_res_path

        res = ada.from_fem_res(default_fem_res_path("cpl", scratch_dir=tmp_path, fem_format=fem_format))
    reac_name = {"code_aster": "REAC_NODA", "sesam": "REACTION-FORCE"}[fem_format]
    reac = next(np.asarray(r.values, float) for r in res.results if reac_name in r.name)
    assert -np.nansum(reac[:, 3]) == pytest.approx(_weight(a), rel=1e-6)
