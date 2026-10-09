"""Sestra solves concept supports converted by ``Part.to_fem_obj``, against hand calculations.

* A cantilever (IPE300, L = 2 m) whose root is held on a dz spring k = 1e6 N/m, every other root dof fixed, and a tip
  load P = -10 kN: the tip moves P/k + PL^3/3EI + PL/(G As). The spring term is 0.01 of the 0.01205 m; with the
  spring dropped the root is free in dz and Sestra has a singular model.
* The same cantilever clamped at the root, its tip a support with dz prescribed and a
  ``LoadConceptPrescribedDisplacement`` of -0.01 m in load case "settle": the tip lands at -0.01 and its reaction
  is that displacement times the tip stiffness 1/(L^3/3EI + L/(G As)).

Measured with Sestra V11.3: -0.0120525146 against -0.0120525149, and -48720.71875 N against -48720.7179 N (a SIN
holds single precision). Skips without Sestra.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import FemSet, Load
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import LoadConceptCase, LoadConceptPrescribedDisplacement
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, P, K, SETTLEMENT = 2.0, -10000.0, 1.0e6, -0.01


def _sestra_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_sestra_exe() is None, reason="Sestra is not installed")


def _beam(name):
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part(name) / bm
    return ada.Assembly(f"{name}_a") / p, p, bm


def _node_at(fem, x) -> int:
    (n,) = [n for n in fem.nodes if abs(n.x - x) < 1e-9 and abs(n.y) < 1e-9 and abs(n.z) < 1e-9]
    return n


def _flexibility(bm) -> tuple[float, float]:
    """A clamped cantilever's tip flexibility in z: bending L^3/3EI and shear L/(G As), As the section's Sharz."""
    props, model = bm.section.properties, bm.material.model
    g = model.E / (2 * (1 + model.v))
    return L**3 / (3 * model.E * props.Iy), L / (g * props.Sharz)


def _solve(a, name, tmp_path):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    a.to_fem(name, "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)
    lis = (tmp_path / name / "SESTRA.LIS").read_text(errors="replace")
    assert "Normal exit from Sestra" in lis, lis[-2000:]
    res = read_sin_file(tmp_path / name / f"{name}R1.SIN")
    ids = list(res.mesh.nodes.identifiers)
    fields = {r.name: np.asarray(r.values) for r in res.results}
    return ids, fields


def test_a_cantilever_on_a_spring_moves_by_p_over_k_and_bends(tmp_path):
    a, p, bm = _beam("spring")
    root = [Dof(d, "fixed") for d in ("dx", "dy", "rx", "ry", "rz")] + [Dof("dz", "spring", K)]
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("Sp_root", (0, 0, 0), root))
    p.fem = p.to_fem_obj(0.5, "line")
    tip = _node_at(p.fem, L)
    step = a.fem.add_step(StepImplicitStatic("LC1", total_time=1.0, init_incr=1.0, max_incr=1.0))
    step.add_load(Load("P", Load.TYPES.FORCE, P, p.fem.add_set(FemSet("tip", [tip], "nset")), dof=[0, 0, 1, 0, 0, 0]))

    ids, fields = _solve(a, "spring", tmp_path)
    bend, shear = _flexibility(bm)
    u = fields["sesam.nodes.displacement"]
    assert u[ids.index(tip.id)][4] == pytest.approx(P / K + P * (bend + shear), rel=1e-6)
    assert u[ids.index(_node_at(p.fem, 0).id)][4] == pytest.approx(P / K, rel=1e-6), "the root rides the spring"


def test_a_prescribed_tip_settlement_lands_and_reacts_as_computed(tmp_path):
    a, p, bm = _beam("settle")
    constraints = p.concept_fem.constraints
    constraints.add_point_constraint(ConstraintConceptPoint("Sp_root", (0, 0, 0), Dof.encastre()))
    tip_dofs = [Dof(d, "free") for d in ("dx", "dy", "rx", "ry", "rz")] + [Dof("dz", "prescribed")]
    sp = constraints.add_point_constraint(ConstraintConceptPoint("Sp_tip", (L, 0, 0), tip_dofs))
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("settle", [LoadConceptPrescribedDisplacement("PD", sp, (0.0, 0.0, SETTLEMENT))])
    )
    p.fem = p.to_fem_obj(0.5, "line")
    tip = _node_at(p.fem, L)
    # A static step, for sestra.inp: Sestra needs its analysis control, and the step has no loads of its own.
    a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=1.0, max_incr=1.0))

    ids, fields = _solve(a, "settle", tmp_path)
    bend, shear = _flexibility(bm)
    assert fields["sesam.nodes.displacement"][ids.index(tip.id)][4] == pytest.approx(SETTLEMENT, rel=1e-6)
    reactions = {int(r[0]): r[1:7] for r in fields["REACTION-FORCE"]}
    assert reactions[tip.id][2] == pytest.approx(SETTLEMENT / (bend + shear), rel=1e-6)
    assert reactions[_node_at(p.fem, 0).id][2] == pytest.approx(-SETTLEMENT / (bend + shear), rel=1e-6)


def _combined_settlements():
    """The cantilever clamped at the root, its tip a support prescribing dx, dz and rz: LC1 moves dx by -0.003 and
    loads mid-span with 10 kN down; LC2 moves dx, dz, rz by 0.005, -0.01, 0.001 (GeniE's frames fixture values);
    LCC = LC1 + LC2."""
    from ada.fem.concept.loads import (
        LoadConceptCaseCombination,
        LoadConceptCaseFactored,
        LoadConceptPoint,
    )

    a, p, bm = _beam("lcc")
    constraints = p.concept_fem.constraints
    constraints.add_point_constraint(ConstraintConceptPoint("Sp_root", (0, 0, 0), Dof.encastre()))
    kinds = {"dx": "prescribed", "dz": "prescribed", "rz": "prescribed"}
    sp = constraints.add_point_constraint(
        ConstraintConceptPoint(
            "Sp_tip", (L, 0, 0), [Dof(d, kinds.get(d, "free")) for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
        )
    )
    loads = p.concept_fem.loads
    lc1 = loads.add_load_case(
        LoadConceptCase(
            "LC1",
            [
                LoadConceptPrescribedDisplacement("PD1", sp, (-0.003, 0.0, 0.0)),
                LoadConceptPoint("P", (L / 2, 0, 0), (0.0, 0.0, P), (0.0, 0.0, 0.0)),
            ],
        )
    )
    lc2 = loads.add_load_case(
        LoadConceptCase("LC2", [LoadConceptPrescribedDisplacement("PD2", sp, (0.005, 0.0, -0.01), (0.0, 0.0, 0.001))])
    )
    terms = [LoadConceptCaseFactored(lc1, 1.0), LoadConceptCaseFactored(lc2, 1.0)]
    loads.add_load_case_combination(LoadConceptCaseCombination("LCC", terms))
    return a, p


def test_a_combination_of_two_settlement_cases_solves_as_their_sum(tmp_path):
    """LCC's every nodal displacement equals LC1's plus LC2's, and its tip lands on the summed settlement (dx 0.002,
    dz -0.01, rz 0.001). Before the combination summed a support's values, the deck gave LCC LC2's tip values only
    and Sestra V11.3 solved it with tip dx 0.005: max |u_LCC - (u_LC1 + u_LC2)| = 0.003, the lost dx. After:
    2.3e-10 against |u| up to 0.01. The bound is a SIN's single precision at that size, 0.01 x 2^-23 = 1.2e-9."""
    a, p = _combined_settlements()
    p.fem = p.to_fem_obj(0.5, "line")
    tip = _node_at(p.fem, L)
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    a.to_fem("lcc", "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)
    lis = (tmp_path / "lcc" / "SESTRA.LIS").read_text(errors="replace")
    assert "Normal exit from Sestra" in lis, lis[-2000:]
    res = read_sin_file(tmp_path / "lcc" / "lccR1.SIN")
    ids = list(res.mesh.nodes.identifiers)
    number = {name: i for i, name in res.sesam_case_names.items()}

    def u(case):
        (values,) = [
            np.asarray(r.values) for r in res.results if r.name == "sesam.nodes.displacement" and r.step == number[case]
        ]
        return values[:, 2:]  # node id, magnitude, then ux uy uz rx ry rz

    u1, u2, uc = u("LC1"), u("LC2"), u("LCC")
    assert uc[ids.index(tip.id)][[0, 2, 5]] == pytest.approx([0.002, -0.01, 0.001], rel=1e-6)
    assert np.abs(uc - (u1 + u2)).max() < 1.2e-9


def test_genies_load_case_numbers_are_sestras_result_case_numbers(tmp_path):
    """The same model with LC1 and LC2 numbered 5 and 9, as GeniE numbers them: Sestra V11.3 gives the result cases
    IERES 5, 9 and 10 (the combination, next up), as it does GeniE V8.13-02's own deck with cases 5 and 9 (IRES 1, 2;
    IERES 5, 9). adapy's deck used to number them 1, 2, 3 whatever GeniE said. The results are still read by name."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    a, p = _combined_settlements()
    cases = p.concept_fem.loads.load_cases
    cases["LC1"].fem_loadcase_number, cases["LC2"].fem_loadcase_number = 5, 9
    p.fem = p.to_fem_obj(0.5, "line")
    a.to_fem("num", "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)
    lis = (tmp_path / "num" / "SESTRA.LIS").read_text(errors="replace")
    assert "Normal exit from Sestra" in lis, lis[-2000:]
    sin = tmp_path / "num" / "numR1.SIN"
    ieres = {int(r[0]): int(r[2]) for r in open_sin(sin).iter_records("RDRESREF")}
    assert ieres == {1: 5, 2: 9, 3: 10}
    assert read_sin_file(sin).sesam_case_names == {1: "LC1", 2: "LC2", 3: "LCC"}
