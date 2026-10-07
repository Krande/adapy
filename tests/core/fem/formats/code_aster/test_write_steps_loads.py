"""The Code_Aster deck's steps and loads, by their text (``tests/fem/test_calculix_code_aster_solve.py`` solves).

* Every load of a step goes under ``EXCIT`` -- only ``step.loads[0]`` did.
* A step of load cases is one ``MACRO_ELAS_MULT``, a ``CAS_CHARGE`` per case with that case's loads.
* Every step is written, into a result of its own; the part's concept step too.
* A point load is ``FORCE_NODALE``; gravity takes its direction from the load.
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem import Load, LoadGravity, LoadPoint, StepImplicitStatic
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptLine,
    LoadConceptPoint,
)
from ada.fem.formats import conversion_report
from ada.materials.metals import CarbonSteel

L = 4.0


def _beam(cases=True):
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    a = ada.Assembly("ss") / p
    fixed = [Dof(d, "fixed" if d in ("dx", "dy", "dz", "rx") else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("pin", (0, 0, 0), fixed))
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("roll", (L, 0, 0), fixed[:3]))
    if cases:
        ld = p.concept_fem.loads
        q = (0, 0, -1000.0)
        ld.add_load_case(LoadConceptCase("LC_u", [LoadConceptLine("u", (0, 0, 0), (L, 0, 0), q, q)]))
        ld.add_load_case(LoadConceptCase("LC_p", [LoadConceptPoint("pm", (2.0, 0, 0), (0, 0, -1e4), (0, 0, 0))]))
        ld.add_load_case(LoadConceptCase("LC_g", [LoadConceptAccelerationField("g", (0, -9.81, 0))]))
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    return a, p, bm


def _comm(a, tmp_path, name="d"):
    with conversion_report.collect() as report:
        a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.comm").read_text(), report


def _excit(comm, result):
    m = re.search(rf"^{result} = MECA_STATIQUE\((.*?)^\)", comm, re.M | re.S)
    assert m, f"no MECA_STATIQUE into {result}"
    return re.findall(r"_F\(CHARGE=(\w+)\)", m[1])


def test_a_step_applies_every_load_it_has(tmp_path):
    a, p, _ = _beam(cases=False)
    fs = p.fem.add_set(ada.fem.FemSet("mid", [p.fem.nodes.get_by_volume((2.0, 0, 0))[0]], "nset"))
    step = a.fem.add_step(StepImplicitStatic("two"))
    step.add_load(LoadPoint("p1", -100.0, fs, 3))
    step.add_load(LoadGravity("grav", -9.81))
    comm, _ = _comm(a, tmp_path)
    assert _excit(comm, "result") == ["bc_pin", "bc_roll", "ld_p1", "ld_grav"]


def test_load_cases_are_one_macro_elas_mult_with_a_case_each(tmp_path):
    a, p, _ = _beam()
    comm, _ = _comm(a, tmp_path)
    m = re.search(r"^result = MACRO_ELAS_MULT\((.*?)^\)", comm, re.M | re.S)
    assert m and "MECA_STATIQUE" not in comm
    assert re.search(r"CHAR_MECA_GLOBAL=\(bc_pin,bc_roll,\)", m[1])
    cases = re.findall(r"_F\(NOM_CAS='(\w+)', CHAR_MECA=\(([^)]*)\), OPTION='SANS'\)", m[1])
    assert cases == [("LC_u", "ld_LC_u_u,"), ("LC_p", "ld_LC_p_pm,"), ("LC_g", "ld_LC_g_g,")]


def test_every_step_is_written_into_a_result_of_its_own_carrying_the_earlier_loads(tmp_path):
    a, p, _ = _beam()
    fs = p.fem.nsets["LC_p_pm"]
    a.fem.add_step(StepImplicitStatic("first")).add_load(LoadPoint("p1", -100.0, fs, 3))
    a.fem.add_step(StepImplicitStatic("second")).add_load(LoadPoint("p2", -200.0, fs, 2))
    comm, report = _comm(a, tmp_path)
    assert _excit(comm, "result") == ["bc_pin", "bc_roll", "ld_p1"]
    assert _excit(comm, "result2") == ["bc_pin", "bc_roll", "ld_p1", "ld_p2"]
    assert "result3 = MACRO_ELAS_MULT(" in comm, "the part's concept step, after the assembly's"
    assert comm.count("IMPR_RESU(") == 3
    assert not [f for f in report.findings if f.keyword == "Step"]


def test_a_point_load_is_force_nodale_and_gravity_has_its_direction(tmp_path):
    a, p, _ = _beam()
    comm, _ = _comm(a, tmp_path)
    assert re.search(
        r"ld_LC_p_pm = AFFE_CHAR_MECA\(\n    MODELE=model,\n    FORCE_NODALE=_F\(GROUP_NO='LC_p_pm', FZ=-10000.0\),", comm
    )
    assert "PESANTEUR=_F(DIRECTION=(0.0, -1.0, 0.0), GRAVITE=9.81)" in comm


def test_a_load_case_with_two_distributed_beam_loads_keeps_its_displacements_and_is_named(tmp_path):
    """Code_Aster computes a beam model's stresses with one distributed load at most (<CALCULEL2_92>, measured): such a
    case is left out of the stress calculation by name; the others are asked for by order number."""
    a, p, _ = _beam()
    ld = p.concept_fem.loads
    q = (0, 0, -1000.0)
    ld.add_load_case(
        LoadConceptCase(
            "LC_ug",
            [LoadConceptLine("u2", (0, 0, 0), (L, 0, 0), q, q), LoadConceptAccelerationField("g2", (0, 0, -9.81))],
        )
    )
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    comm, report = _comm(a, tmp_path)
    assert re.search(r"CONTRAINTE=.*", comm)
    assert "NUME_ORDRE=(1, 2, 3,)," in comm
    (left,) = [f for f in report.findings if f.keyword == "CALC_CHAMP"]
    assert (left.kind, left.subject) == ("omitted", "LC_ug")


def test_a_load_of_a_kind_it_has_no_form_for_is_refused_by_name(tmp_path):
    from ada.fem.exceptions.model_definition import UnsupportedLoadType

    a, p, _ = _beam(cases=False)
    a.fem.add_step(StepImplicitStatic("s")).add_load(Load("m", Load.TYPES.MASS, 1.0))
    with pytest.raises(UnsupportedLoadType, match="'m'"):
        _comm(a, tmp_path)


def test_a_later_load_where_abaqus_would_replace_an_earlier_one_is_reported(tmp_path):
    """Step 2 loads the node and dof step 1 loaded: Abaqus (and ccx, per node and dof) replaces the earlier load, the
    writers add the two -- said by name. A load on another dof of the same node is no such case."""
    a, p, _ = _beam(cases=False)
    fs = p.fem.add_set(ada.fem.FemSet("mid", [p.fem.nodes.get_by_volume((2.0, 0, 0))[0]], "nset"))
    a.fem.add_step(StepImplicitStatic("first")).add_load(LoadPoint("p1", -100.0, fs, 3))
    a.fem.add_step(StepImplicitStatic("second")).add_load(LoadPoint("p2", -200.0, fs, 2))
    a.fem.add_step(StepImplicitStatic("third")).add_load(LoadPoint("p3", -300.0, fs, 3))
    _, report = _comm(a, tmp_path)
    (found,) = [f for f in report.findings if f.keyword == "Step"]
    assert (found.kind, found.subject, found.details) == ("approximated", "third", {"load": "p1"})
