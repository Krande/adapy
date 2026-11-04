"""A load case of several distributed loads on a beam model gets its stresses and element forces from Code_Aster.

A 4 m IPE300 cantilever along x (clamped at x = 0) in one load case: 5 kN/m down over each half of the span (two line
loads), gravity, and two 1 kN point loads down on the tip node. The root shear is ``(q + rho A g) L + 2 P`` and the
root moment ``(q + rho A g) L^2 / 2 + 2 P L``.

Code_Aster computes a beam model's stresses over one charge with distributed loads at most (<CALCULEL2_92>, which
counts charges). Measured before the fix (Code_Aster 18.1.11): each load was its own charge, and the run stopped at
<CALCULEL2_92> already in CALC_CHAMP(FORCE='REAC_NODA') -- no result at all; without the gravity the case was left out
of CALC_CHAMP (no EFGE_ELNO, SIEF_ELNO or SIPO_ELNO) with displacements and reactions only, as a case of eight line
loads on a shell-and-beam model was. After it, the distributed loads share one charge and the point
loads keep their own: two items on one node in one charge do not add (the later replaces the earlier), which put
18.3 kN of that eight-line-load case's load out when its end point loads were merged too.
"""

from __future__ import annotations

import h5py
import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadCase, LoadLine, LoadPoint, StepImplicitStatic
from ada.fem.loads.fe_loads import LineLoadSegment
from ada.materials.metals import CarbonSteel

L, Q, G, P = 4.0, 5000.0, 9.81, 1000.0


def _model() -> ada.Assembly:
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    root = [n for n in p.fem.nodes if abs(n.x) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - L) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("root", root, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    tip_a = p.fem.add_set(FemSet("tip_a", tip, FemSet.TYPES.NSET))
    tip_b = p.fem.add_set(FemSet("tip_b", tip, FemSet.TYPES.NSET))
    a = ada.Assembly("A") / p
    q = (0.0, 0.0, -Q)
    first = [el for el in p.fem.elements.lines if max(n.x for n in el.nodes) <= L / 2 + 1e-9]
    second = [el for el in p.fem.elements.lines if min(n.x for n in el.nodes) >= L / 2 - 1e-9]
    loads = [
        LoadLine("q_first", [LineLoadSegment(el, q, q) for el in first]),
        LoadLine("q_second", [LineLoadSegment(el, q, q) for el in second]),
        ada.fem.LoadGravity("grav", -G),
        LoadPoint("p_a", -P, tip_a, 3),
        LoadPoint("p_b", -P, tip_b, 3),
    ]
    step = a.fem.add_step(StepImplicitStatic("static"))
    for ld in loads:
        step.add_load(ld)
    step.add_loadcase(LoadCase("all", None, loads=loads))
    return a


def test_a_case_of_several_distributed_loads_has_element_forces(tmp_path, require_solver):
    require_solver("code_aster")
    a = _model()
    res = a.to_fem("cs", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    assert res.step_name_map == {1: "all"}  # the load case by name, as Sesam's reader gives it
    bm = next(iter(a.get_all_physical_objects(by_type=ada.Beam)))
    w = bm.material.model.rho * bm.section.properties.Ax * G
    reac = next(np.asarray(r.values, float) for r in res.results if "REAC_NODA" in r.name)
    assert np.nansum(reac[:, 3]) == pytest.approx((Q + w) * L + 2 * P, rel=1e-6)
    with h5py.File(tmp_path / "cs" / "cs.rmed", "r") as f:
        assert "result__EFGE_ELNO" in f["CHA"], "the case's element forces were not computed"
        g = f["CHA/result__EFGE_ELNO"]
        comps = g.attrs["NOM"].decode().split()
        d = g[sorted(g.keys())[0]]["NOE.SE2"]["MED_NO_PROFILE_INTERNAL"]
        nbr, nga = int(d.attrs["NBR"]), int(d.attrs["NGA"])
        efge = d["CO"][()].reshape(len(comps), nbr, nga)
        m = f["ENS_MAA"][list(f["ENS_MAA"].keys())[0]]
        ts = m[list(m.keys())[0]]
        x = ts["NOE/COO"][()].reshape(3, -1)[0]
        cells = ts["MAI/SE2/NOD"][()].reshape(2, -1).T - 1
    (root_cell,) = [i for i, c in enumerate(cells) if abs(x[c[0]]) < 1e-9 or abs(x[c[1]]) < 1e-9]
    end = 0 if abs(x[cells[root_cell][0]]) < 1e-9 else 1
    moment = np.hypot(efge[comps.index("MFY"), root_cell, end], efge[comps.index("MFZ"), root_cell, end])
    assert moment == pytest.approx((Q + w) * L**2 / 2 + 2 * P * L, rel=1e-6)


def test_overlapping_distributed_loads_keep_their_own_charges(tmp_path):
    """Two line loads on the same elements: one charge would keep the later and drop the earlier there, so each keeps
    its own charge and the case is left out of the stress calculation, reported."""
    from ada.fem.formats import conversion_report

    a = _model()
    step = a.fem.steps[0]
    p = a.get_part("P")
    q = (0.0, 0.0, -Q)
    twice = LoadLine("q_again", [LineLoadSegment(el, q, q) for el in p.fem.elements.lines])
    step.add_load(twice)
    step.add_loadcase(LoadCase("overlap", None, loads=[step.load_cases["all"].loads[0], twice]))
    with conversion_report.collect() as report:
        a.to_fem("ov", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "ov" / "ov.comm").read_text()
    assert "result_c1 = AFFE_CHAR_MECA" in comm and "result_c2" not in comm
    assert [f.subject for f in report.findings if f.keyword == "CALC_CHAMP"] == ["overlap"]
