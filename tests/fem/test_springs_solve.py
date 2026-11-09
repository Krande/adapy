"""Springs against their closed form in every solver: a 4 m IPE300 cantilever whose tip stands on a grounded spring
``k`` along z, pressed down at the tip by ``P``, settles by ``P / (k + 3 E I / L^3)``. ``k`` is a hundred times the
beam's own tip stiffness, so the beam's shear (which CalculiX's U1 beam and Sestra's have, Code_Aster's POU_D_E not)
moves the settlement by under 2e-4. The spring on the part, and on the assembly (``assembly.fem``, merged onto the
part's node for the single-part writers).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, Spring, StepImplicitStatic
from ada.fem.outputs import FieldOutput
from ada.materials.metals import CarbonSteel

L, P = 4.0, 1.0e4


def _model(on_assembly: bool):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat, up=(0, 0, 1))
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    beam_k = 3 * mat.model.E * bm.section.properties.Iy / L**3
    k = 100 * beam_k
    holder = a.fem if on_assembly else fem
    spring_set = holder.add_set(FemSet("spring_at", [tip], "nset"))
    holder.add_spring(
        Spring("k", 9000, "SPRING1", fem_set=spring_set, stiff=np.diag([0, 0, k, 0, 0, 0]), parent=holder)
    )
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
    step.add_load(LoadPoint("p", -P, fem.add_set(FemSet("tip", [tip], "nset")), 3))
    return a, tip, -P / (k + beam_k)


@pytest.mark.parametrize("on_assembly", [False, True], ids=["part", "assembly"])
@pytest.mark.parametrize("solver", ["code_aster", "calculix", "sesam"])
def test_a_spring_held_cantilever_settles_by_p_over_k_plus_its_own_stiffness(
    tmp_path, require_solver, solver, on_assembly
):
    require_solver(solver)
    a, tip, want = _model(on_assembly)
    res = a.to_fem("sp", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = res.case_fields("nodal_displacement").items()
    (u,) = [f for f in fields if getattr(f.role, "value", f.role) == "nodal_displacement"]
    (row,) = [r for r in np.asarray(u.values, float) if int(r[0]) == int(tip.id)]
    assert row[3] == pytest.approx(want, rel=5e-4 if solver == "code_aster" else 1e-3)


def test_calculix_writes_a_grounded_spring_and_refuses_a_rotational_one(tmp_path):
    from ada.fem.formats import conversion_report

    a, *_ = _model(False)
    a.to_fem("d", "calculix", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "d" / "d.inp").read_text()
    assert "*Element, type=SPRING1, elset=ADA_SPRING_k_3" in inp and "*Spring, elset=ADA_SPRING_k_3\n 3\n" in inp
    a, *_ = _model(False)
    (sp,) = a.get_part("P").fem.springs.values()
    sp._stiff = np.diag([0, 0, 1e6, 1e3, 0, 0])
    with conversion_report.collect() as rep:
        a.to_fem("e", "calculix", scratch_dir=tmp_path, overwrite=True)
    assert [f for f in rep.findings if f.kind == "omitted" and f.subject == "k"]
