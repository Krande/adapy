"""Several static steps in one model, solved step by step: each Sesam load case, and each Code_Aster step, is that
step's state.

adapy's steps follow Abaqus: a general step's loads carry into the next general step. A cantilever along x, clamped at
its root, carries a tip force F along z in step 1 and another along y in step 2; step 2 is solved for both. The root
reactions are statics': ``R = -F e_z`` after step 1, ``R = -F (e_y + e_z)`` after step 2, exact whatever the beam.

The Sesam writer wrote the first step only and dropped the rest (reported). It now writes each static step as a load
case of the deck, named after the step, with the loads it is solved for (``write_steps.written_steps``).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, F = 3.0, 1.0e4


def _two_steps():
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    tip = fem.add_set(FemSet("tip", [n for n in fem.nodes if abs(n.x - L) < 1e-9], "nset"))
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    s1 = a.fem.add_step(StepImplicitStatic("down"))
    s1.add_load(LoadPoint("fz", F, tip, 3))
    s2 = a.fem.add_step(StepImplicitStatic("sideways"))
    s2.add_load(LoadPoint("fy", F, tip, 2))
    return a, root


WANT = {"down": (0.0, 0.0, -F), "sideways": (0.0, -F, -F)}


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_each_static_step_is_solved_with_the_loads_it_carries(tmp_path, require_solver, solver):
    require_solver(solver)
    a, root = _two_steps()
    res = a.to_fem("steps", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    by_case = res.case_fields("nodal_reaction")
    assert len(by_case) == 2, list(by_case)
    got = {}
    for case, fields in by_case.items():
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == "nodal_reaction"]
        v = np.asarray(f.values, float)
        # the FieldRole layout, [id, x, y, z, rx, ry, rz], whatever each solver labels its columns
        (row,) = [r for r in v if int(r[0]) == int(root.id)]
        got[str(res.cases.names[case])] = row[1:4]
    rel = 1e-5 if solver == "sesam" else 1e-9
    for step, want in WANT.items():
        (key,) = [k for k in got if step in k.lower()] or [None]
        assert key is not None, (step, list(got))
        assert got[key] == pytest.approx(want, rel=rel, abs=1e-6 * F), step


def test_the_sesam_deck_holds_one_load_case_per_static_step(tmp_path):
    """No solver: two TDLOAD cases named after the steps; the second carries the first's BNLOAD too."""
    a, _ = _two_steps()
    a.to_fem("steps", "sesam", scratch_dir=tmp_path, overwrite=True)
    text = next((tmp_path / "steps").glob("*T1.FEM")).read_text()
    lines = text.splitlines()
    names = [lines[i + 1].strip() for i, ln in enumerate(lines) if ln.startswith("TDLOAD")]
    assert names == ["down", "sideways"]
    llcs = [int(float(ln.split()[1])) for ln in lines if ln.startswith("BNLOAD")]
    assert llcs == [1, 2, 2]  # fz in case 1; fz carried and fy in case 2


def test_the_first_steps_own_support_is_written_and_a_later_steps_stops_the_deck(tmp_path):
    """No solver. The first step's own Bc holds in every step after it, so it goes into BNBCD; a later step with a
    Bc of its own would hold it in the earlier cases too, so the deck stops before that step, reported by name."""
    from ada.fem.formats import conversion_report

    a, root = _two_steps()
    p = a.get_part("P")
    (s1, s2) = a.fem.steps
    p.fem.bcs.clear()
    s1.add_bc(Bc("step_fix", p.fem.add_set(FemSet("root2", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    s3 = a.fem.add_step(StepImplicitStatic("third"))
    s3.add_bc(Bc("late", p.fem.add_set(FemSet("late_set", [root], "nset")), [1]))
    with conversion_report.collect() as rep:
        a.to_fem("steps", "sesam", scratch_dir=tmp_path, overwrite=True)
    lines = next((tmp_path / "steps").glob("*T1.FEM")).read_text().splitlines()
    names = [lines[i + 1].strip() for i, ln in enumerate(lines) if ln.startswith("TDLOAD")]
    assert names == ["down", "sideways"]
    (bnbcd,) = [ln for ln in lines if ln.startswith("BNBCD") and int(float(ln.split()[1])) == int(root.id)]
    assert bnbcd.split()[3:5] == ["1.00000000E+00", "1.00000000E+00"]
    found = [f for f in rep._findings if f.subject == "third"]
    assert found and found[0].kind == "omitted"
    assert not [f for f in rep._findings if f.subject == "step_fix"]
