"""A prescribed displacement in each load case, and a rigid link's slave rotations, through the Sesam and Abaqus
writers.

GeniE gives one support a value in each load case (V8.13-02: -0.003 in dx in LC1; 0.005, -0.01, 0.001 in dx, dz, rz
in LC2), which adapy holds as one ``Bc`` per case naming the case in ``metadata[BC_LOAD_CASE]``. The Sesam deck takes
them as one BNDISPL per case; the Abaqus deck holds the dofs at zero in model data (Abaqus 2025: "PRESCRIBED
*BOUNDARY MAGNITUDES MUST BE ZERO IN THE MODEL DEFINITION") and gives the values in the step of the case.
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem import Bc, Constraint, FemSet
from ada.fem.constraints import BC_LOAD_CASE
from ada.fem.formats import conversion_report
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel


def _cantilever(with_steps=("LC1", "LC2"), tagged=True):
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - 2) < 1e-9]
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    tip_set = fem.add_set(FemSet("tip_set", [tip], "nset"))
    fem.add_bc(Bc("tip", tip_set, [1, 3, 6]))
    cases = {"LC1": [-0.003, 0.0, 0.0], "LC2": [0.005, -0.01, 0.001]}
    for case, values in cases.items():
        meta = {BC_LOAD_CASE: case} if tagged else None
        fem.add_bc(Bc(f"tip_{case}", tip_set, [1, 3, 6], magnitudes=values, metadata=meta))
    for name in with_steps:
        a.fem.add_step(StepImplicitStatic(name, total_time=1.0, init_incr=1.0, max_incr=1.0))
    return a, tip.id


def _bndispl(deck: str) -> dict[str, dict[int, list[float]]]:
    """``{case name: {node: six values}}`` from the TDLOAD names and BNDISPL records."""
    names = {int(float(m[1])): m[2] for m in re.finditer(r"^TDLOAD\s+\S+\s+(\S+).*\n\s+(\S+)", deck, re.M)}
    out: dict[str, dict[int, list[float]]] = {}
    for m in re.finditer(r"^BNDISPL(.*\n(?: .*\n){2})", deck, re.M):
        v = [float(x) for x in m[1].split()]
        out.setdefault(names[int(v[0])], {})[int(v[4])] = v[6:12]
    return out


def test_each_load_case_gets_its_own_bndispl(tmp_path):
    a, tip = _cantilever(with_steps=())
    a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "presc" / "prescT1.FEM").read_text()
    assert _bndispl(deck) == {
        "LC1": {tip: [-0.003, 0.0, 0.0, 0.0, 0.0, 0.0]},
        "LC2": {tip: [0.005, 0.0, -0.01, 0.0, 0.0, 0.001]},
    }
    # FIX code 2 on dx, dz, rz of the tip, 1 on dy
    assert re.search(
        rf"^BNBCD\s+{tip}\.0+E\+00\s+6\.0+E\+00\s+2\.0+E\+00\s+0\.0+E\+00\n\s+2\.0+E\+00\s+0\.0+E\+00\s+0\.0+E\+00\s+2\.0+E\+00",
        deck,
        re.M,
    )  # noqa: E501


def test_an_untagged_settlement_stays_in_the_first_case(tmp_path):
    a, tip = _cantilever(with_steps=(), tagged=False)
    with conversion_report.collect():
        a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "presc" / "prescT1.FEM").read_text()
    assert list(_bndispl(deck)) == ["LC1"]


def test_abaqus_holds_the_dofs_in_model_data_and_values_them_per_step(tmp_path):
    a, _ = _cantilever()
    with conversion_report.collect() as report:
        a.to_fem("presc", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "presc" / "presc.inp").read_text()
    model, _, steps = inp.partition("*Step")
    # model data: no magnitude anywhere, and each tip dof held once
    tip_lines = re.findall(r"^ \S*tip_set, (\d), \1(.*)$", model, re.M)
    assert sorted(d for d, _ in tip_lines) == ["1", "3", "6"]
    assert all(rest == "" for _, rest in tip_lines)
    lc1, lc2 = steps.split("*Step")[0], steps.split("*Step")[1]
    assert re.findall(r"tip_set, (\d), \d, (\S+)", lc1) == [("1", "-0.003"), ("3", "0.0"), ("6", "0.0")]
    assert re.findall(r"tip_set, (\d), \d, (\S+)", lc2) == [("1", "0.005"), ("3", "-0.01"), ("6", "0.001")]
    assert not report.has_omissions


def test_abaqus_resets_another_cases_settlement_in_a_step_of_its_own(tmp_path):
    a, _ = _cantilever(with_steps=("LC1", "other"))
    with conversion_report.collect() as report:
        a.to_fem("presc", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "presc" / "presc.inp").read_text()
    other = inp.split("*Step, name=other")[1]
    assert re.findall(r"tip_set, (\d), \d, (\S+)", other) == [("1", "0."), ("3", "0."), ("6", "0.")]
    (finding,) = [f for f in report.findings if f.keyword == "*Boundary"]
    assert (finding.kind, finding.subject, finding.details["load_case"]) == ("omitted", "tip_LC2", "LC2")


def test_a_lone_settlement_is_held_in_model_data_and_valued_in_the_step(tmp_path):
    """The Sesam reader's shape: one ``Bc`` holding the dof and carrying its value. Abaqus refuses the value in model
    data (fatal: "PRESCRIBED *BOUNDARY MAGNITUDES MUST BE ZERO IN THE MODEL DEFINITION"), which adapy wrote there."""
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, "line")
    (tip,) = [n for n in p.fem.nodes if abs(n.x - 1) < 1e-9]
    p.fem.add_bc(Bc("settle", p.fem.add_set(FemSet("tip_set", [tip], "nset")), [3], magnitudes=[-0.01]))
    a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=1.0, max_incr=1.0))
    a.to_fem("lone", "abaqus", scratch_dir=tmp_path, overwrite=True)
    model, _, step = (tmp_path / "lone" / "lone.inp").read_text().partition("*Step")
    assert re.findall(r"^ \S*tip_set, 3, 3(.*)$", model, re.M) == [""]
    assert re.findall(r"^ \S*tip_set, 3, 3(.*)$", step, re.M) == [", -0.01"]


def _linked(rotation_dependent: bool):
    a = ada.Assembly("a") / (ada.Part("p") / ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01))
    (p,) = a.get_all_subparts()
    p.fem = p.to_fem_obj(0.5, use_quads=True)
    m = p.fem.nodes.add(ada.Node((0.5, 0.5, 1.0)))
    slaves = [n for n in p.fem.nodes if abs(n.z) < 1e-9 and n.x < 0.6]
    dofs = [1, 2, 3, 4, 5, 6] if rotation_dependent else [1, 2, 3]
    p.fem.add_constraint(
        Constraint("rl", "coupling", FemSet("rl_m", [m], "nset"), FemSet("rl_s", slaves, "nset"), dofs=dofs)
    )
    p.fem.add_bc(Bc("rl_bc", p.fem.add_set(FemSet("rl_bc_set", [m], "nset")), [1, 2, 3, 4, 5, 6]))
    return a, len(slaves)


@pytest.mark.parametrize("rotation_dependent, n_terms", [(True, 12), (False, 9)])
def test_a_couplings_slave_rotations_follow_the_master(tmp_path, rotation_dependent, n_terms):
    """GeniE's rotation dependent rigid link is a 12-term BLDEP with BNBCD 3 on all six slave dofs; translations only,
    9 terms and 3 on dofs 1-3. Read back, the coupling holds the same dofs."""
    a, n_slaves = _linked(rotation_dependent)
    with conversion_report.collect() as report:
        a.to_fem("rl", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "rl" / "rlT1.FEM").read_text()
    counts = [int(float(m[1])) for m in re.finditer(r"^BLDEP\s+\S+\s+\S+\s+\S+\s+(\S+)", deck, re.M)]
    assert counts == [n_terms] * n_slaves
    assert not [f for f in report.findings if f.keyword == "Constraint"]
    b = ada.from_fem(tmp_path / "rl" / "rlT1.FEM")
    (con,) = [c for p in b.get_all_parts_in_assembly() for c in p.fem.constraints.values()]
    assert list(con.dofs) == ([1, 2, 3, 4, 5, 6] if rotation_dependent else [1, 2, 3])
