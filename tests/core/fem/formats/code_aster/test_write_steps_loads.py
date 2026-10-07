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
    assert _excit(comm, "result") == ["supports", "ld_p1", "ld_grav"]


def test_load_cases_are_one_macro_elas_mult_with_a_case_each(tmp_path):
    a, p, _ = _beam()
    comm, _ = _comm(a, tmp_path)
    m = re.search(r"^result = MACRO_ELAS_MULT\((.*?)^\)", comm, re.M | re.S)
    assert m and "MECA_STATIQUE" not in comm
    assert re.search(r"CHAR_MECA_GLOBAL=\(supports,\)", m[1])
    cases = re.findall(r"_F\(NOM_CAS='(\w+)', CHAR_MECA=\(([^)]*)\), OPTION='SANS'\)", m[1])
    assert cases == [("LC_u", "ld_LC_u_u,"), ("LC_p", "ld_LC_p_pm,"), ("LC_g", "ld_LC_g_g,")]


def test_every_step_is_written_into_a_result_of_its_own_carrying_the_earlier_loads(tmp_path):
    a, p, _ = _beam()
    fs = p.fem.nsets["LC_p_pm"]
    a.fem.add_step(StepImplicitStatic("first")).add_load(LoadPoint("p1", -100.0, fs, 3))
    a.fem.add_step(StepImplicitStatic("second")).add_load(LoadPoint("p2", -200.0, fs, 2))
    comm, report = _comm(a, tmp_path)
    assert _excit(comm, "result") == ["supports", "ld_p1"]
    assert _excit(comm, "result2") == ["supports", "ld_p1", "ld_p2"]
    assert "result3 = MACRO_ELAS_MULT(" in comm, "the part's concept step, after the assembly's"
    assert comm.count("IMPR_RESU(") == 3
    assert not [f for f in report.findings if f.keyword == "Step"]


def test_a_step_support_holds_in_its_step_and_every_later_one(tmp_path):
    """A ``Bc`` added to step 2 (``Step.add_bc``) holds in steps 2 and 3, as in Abaqus and CalculiX: each of those
    steps is solved with a supports charge of its own holding the model's supports and the step's in one charge; step 1
    keeps the model's. It was left out of the deck without a finding."""
    from ada.fem import Bc

    a, p, _ = _beam(cases=False)
    fs = p.fem.add_set(ada.fem.FemSet("mid", [p.fem.nodes.get_by_volume((2.0, 0, 0))[0]], "nset"))
    a.fem.add_step(StepImplicitStatic("first")).add_load(LoadPoint("p1", -100.0, fs, 3))
    second = a.fem.add_step(StepImplicitStatic("second"))
    second.add_bc(Bc("mid_y", fs, [2]))
    second.add_load(LoadPoint("p2", -200.0, fs, 3))
    a.fem.add_step(StepImplicitStatic("third")).add_load(LoadPoint("p3", -300.0, fs, 3))
    comm, _ = _comm(a, tmp_path)
    assert _excit(comm, "result") == ["supports", "ld_p1"]
    assert _excit(comm, "result2") == ["supports_2", "ld_p1", "ld_p2"]
    assert _excit(comm, "result3") == ["supports_3", "ld_p1", "ld_p2", "ld_p3"]
    for k in (2, 3):
        charge = re.search(rf"^supports_{k} = AFFE_CHAR_MECA\((.*?)^\)", comm, re.M | re.S)[1]
        assert re.findall(r'_F\(GROUP_NO="(\w+)", [^)]*\),  # (\w+)', charge) == [
            ("pin_set", "pin"),
            ("roll_set", "roll"),
            ("mid", "mid_y"),
        ]
    supports = re.search(r"^supports = AFFE_CHAR_MECA\((.*?)^\)", comm, re.M | re.S)[1]
    assert "mid_y" not in supports


def test_a_point_load_is_force_nodale_and_gravity_has_its_direction(tmp_path):
    a, p, _ = _beam()
    comm, _ = _comm(a, tmp_path)
    assert re.search(
        r"ld_LC_p_pm = AFFE_CHAR_MECA\(\n    MODELE=model,\n    FORCE_NODALE=_F\(GROUP_NO='LC_p_pm', FZ=-10000.0\),",
        comm,
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


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
def test_line_loads_on_other_elements_in_later_steps_are_not_reported_as_replaced(tmp_path, fem_format):
    """General steps, each a uniform line load on one beam element: along z on elements 1, 2, then 1 again, then
    along y on element 1. Abaqus replaces a ``*Dload`` per element and load label, so only step 3's load replaces an
    earlier one (step 1's); step 2's, on element 2, and step 4's, a PY where the others are PZ, add -- as they do here.

    Measured before: a ``LoadLine`` has no node set or surface, so every one was keyed ``(LINE, None)`` and each
    carried line load was reported ``approximated`` ("Abaqus replaces the earlier load") in every later step: the first
    finding was step 2's, for an element step 1 did not load."""
    from ada.fem import LoadLine
    from ada.fem.loads.fe_loads import LineLoadSegment

    a, p, _ = _beam(cases=False)
    el1, el2, el3 = sorted(p.fem.elements.lines, key=lambda e: e.id)[:3]
    q = (0.0, 0.0, -1000.0)
    a.fem.add_step(StepImplicitStatic("first")).add_load(LoadLine("q1", [LineLoadSegment(el1, q, q)]))
    a.fem.add_step(StepImplicitStatic("second")).add_load(LoadLine("q2", [LineLoadSegment(el2, q, q)]))
    a.fem.add_step(StepImplicitStatic("third")).add_load(LoadLine("q3", [LineLoadSegment(el1, q, q)]))
    qy = (0.0, -1000.0, 0.0)
    a.fem.add_step(StepImplicitStatic("fourth")).add_load(LoadLine("q4", [LineLoadSegment(el1, qy, qy)]))
    # A varying load is written as nodal *Cload lines: on element 2 it adds to step 2's *Dload there; on element 3,
    # which shares a node with element 2, it replaces step 5's nodal force at that node.
    q_hi = (0.0, 0.0, -2000.0)
    a.fem.add_step(StepImplicitStatic("fifth")).add_load(LoadLine("q5", [LineLoadSegment(el2, q, q_hi)]))
    assert {n.id for n in el2.nodes} & {n.id for n in el3.nodes}
    a.fem.add_step(StepImplicitStatic("sixth")).add_load(LoadLine("q6", [LineLoadSegment(el3, q, q_hi)]))
    with conversion_report.collect() as report:
        a.to_fem("ll", fem_format, scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    (found,) = [f for f in report.findings if f.keyword == "Step"]  # one finding, counted per occurrence
    assert (found.kind, found.subject, found.details["load"]) == ("approximated", "third", "q1")
    assert (found.count, found.other_subjects) == (2, ["sixth"])


def _settling_beam():
    """The beam on a pin and a support whose dz is prescribed: -0.01 m in LC_s, a point load alone in LC_p."""
    from ada.fem.concept.loads import LoadConceptPrescribedDisplacement

    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    a = ada.Assembly("ss") / p
    c = p.concept_fem.constraints
    c.add_point_constraint(ConstraintConceptPoint("root", (0, 0, 0), Dof.encastre()))
    tip = [Dof(d, "free") for d in ("dx", "dy", "rx", "ry", "rz")] + [Dof("dz", "prescribed")]
    sp = c.add_point_constraint(ConstraintConceptPoint("tip", (L, 0, 0), tip))
    ld = p.concept_fem.loads
    ld.add_load_case(LoadConceptCase("LC_p", [LoadConceptPoint("pm", (2.0, 0, 0), (0, 0, -1e3), (0, 0, 0))], 1))
    ld.add_load_case(LoadConceptCase("LC_s", [LoadConceptPrescribedDisplacement("pd", sp, (0, 0, -0.01))], 2))
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    return a, p


def test_a_prescribed_dof_is_held_once_and_takes_its_value_case_by_case(tmp_path):
    """The support's ``DZ=0`` and the settlement's both held the tip in ``CHAR_MECA_GLOBAL``, and Code_Aster stops
    on a dof held twice (<ASSEMBLA_26>, measured); the value was not written at all. Now the support's charge leaves
    the prescribed dof out, and the step -- which ``MACRO_ELAS_MULT`` cannot give a support value per case
    (<ASSEMBLA_45>, measured) -- is one ``MECA_STATIQUE`` with an instant per case: the settlement a unit charge whose
    ``FONC_MULT`` is 0 at instant 1 and -0.01 at instant 2, the point load's 1 then 0."""
    a, p = _settling_beam()
    comm, report = _comm(a, tmp_path)
    assert not report.of_kind("omitted"), report.summary()
    supports = re.search(r"^supports = AFFE_CHAR_MECA\((.*?)^\)", comm, re.M | re.S)[1]
    assert 'GROUP_NO="tip_set"' not in supports, "the tip support holds dz only, which the settlement prescribes"
    assert (
        'result_p1 = AFFE_CHAR_MECA(\n    MODELE=model,\n    DDL_IMPO=(\n        _F(GROUP_NO="tip_set", DZ=1.0),'
        in comm
    )
    assert "MACRO_ELAS_MULT" not in comm
    assert "result_t = DEFI_LIST_REEL(VALE=(1.0, 2.0,))" in comm
    assert "result_g1 = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, 1.0, 0.0, 2.0, -0.01))" in comm
    assert "result_f1 = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, 1.0, 1.0, 2.0, 0.0))" in comm
    m = re.search(r"^result = MECA_STATIQUE\((.*?)^\)", comm, re.M | re.S)
    assert re.findall(r"_F\(CHARGE=(\w+)(?:, FONC_MULT=(\w+))?\)", m[1]) == [
        ("supports", ""),
        ("ld_LC_p_pm", "result_f1"),
        ("result_p1", "result_g1"),
    ], "each dof held once: the support at zero, the prescribed dz by its own charge"


def test_a_general_step_gives_the_prescribed_dof_its_value_and_other_steps_hold_it_at_zero(tmp_path):
    """A settlement naming no load case belongs to every static step: a general step holds the dof with a charge of
    the value (``result_pd``) instead of the one at zero, which the eigen step keeps."""
    from ada.fem import Bc, StepEigen

    a, p, _ = _beam(cases=False)
    p.fem.add_bc(Bc("roll_settle", p.fem.nsets["roll_set"], [3], magnitudes=[-0.02]))
    mid = p.fem.add_set(ada.fem.FemSet("mid", [p.fem.nodes.get_by_volume((2.0, 0, 0))[0]], "nset"))
    a.fem.add_step(StepImplicitStatic("lin")).add_load(LoadPoint("p1", -100.0, mid, 3))
    a.fem.add_step(StepEigen("eig", num_eigen_modes=3))
    comm, report = _comm(a, tmp_path)
    assert not report.of_kind("omitted"), report.summary()
    roll = re.search(r'_F\(GROUP_NO="roll_set", (.*?)\),  # roll\n', comm)
    assert roll and "DZ" not in roll[1] and "DX=0.0" in roll[1], "the support leaves the prescribed dz out"
    pd = 'result_pd = AFFE_CHAR_MECA(\n    MODELE=model,\n    DDL_IMPO=(\n        _F(GROUP_NO="roll_set", DZ=-0.02),'
    zero = (
        'prescribed_zero = AFFE_CHAR_MECA(\n    MODELE=model,\n    DDL_IMPO=(\n        _F(GROUP_NO="roll_set", DZ=0.0),'
    )
    assert pd in comm and zero in comm
    assert _excit(comm, "result") == ["supports", "result_pd", "ld_p1"]
    assert "CHARGE=(supports, prescribed_zero,)," in comm


def test_overlapping_supports_are_one_charge_each_node_held_in_the_union_of_its_dofs(tmp_path):
    """The pin and a second support on the pin's node holding dz and ry: two charges held dz there twice and
    Code_Aster stopped (<ASSEMBLA_26>, measured on a strip's corner). Now every support is a row of one charge,
    ``supports``, and a dof given twice in one ``AFFE_CHAR_MECA`` is held once (measured: the strip solves, reactions
    q A to 4e-16)."""
    from ada.fem import Bc

    a, p, _ = _beam(cases=False)
    pin = p.fem.nsets["pin_set"]
    p.fem.add_bc(Bc("pin_ry", p.fem.add_set(ada.fem.FemSet("pin2", list(pin.members), "nset")), [3, 5]))
    mid = p.fem.add_set(ada.fem.FemSet("mid", [p.fem.nodes.get_by_volume((2.0, 0, 0))[0]], "nset"))
    a.fem.add_step(StepImplicitStatic("lin")).add_load(LoadPoint("p1", -100.0, mid, 3))
    comm, _ = _comm(a, tmp_path)
    supports = re.search(r"^supports = AFFE_CHAR_MECA\((.*?)^\)", comm, re.M | re.S)[1]
    assert re.findall(r'_F\(GROUP_NO="(\w+)", ([^)]*)\),  # (\w+)', supports) == [
        ("pin_set", "DX=0.0, DY=0.0, DZ=0.0, DRX=0.0", "pin"),
        ("roll_set", "DX=0.0, DY=0.0, DZ=0.0, DRX=0.0, DRY=0.0, DRZ=0.0", "roll"),
        ("pin2", "DZ=0.0, DRY=0.0", "pin_ry"),
    ]
    assert comm.count("= AFFE_CHAR_MECA(") == 2, "the supports and the point load"
    assert _excit(comm, "result") == ["supports", "ld_p1"]


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("other", ["support", "settlement", "step support"])
def test_a_prescribed_dof_another_condition_holds_on_the_same_node_is_refused_by_name(tmp_path, other, fem_format):
    """The settling beam's tip (dz prescribed, -0.01 m in LC_s) also in another node set whose Bc holds dz at 0 (in
    the model or in the step), or prescribes it +0.02: one value would have to win. As two charges Code_Aster stops
    (<ASSEMBLA_26>); CalculiX took it without a word, the step's value replacing the hold (measured on a plate strip,
    ccx 2.23: the root reaction of the strip without the hold, 4.508286 N). Both writers refuse it at write time,
    naming the node, the dof and both conditions."""
    from ada.fem import Bc
    from ada.fem.exceptions.model_definition import ConflictingBoundaryConditions

    a, p = _settling_beam()
    tip = p.fem.nodes.get_by_volume((L, 0, 0))[0]
    fs = p.fem.add_set(ada.fem.FemSet("tip2", [tip], "nset"))
    extra = Bc("tip_extra", fs, [3], magnitudes=[0.02] if other == "settlement" else None)
    if other == "step support":
        p.fem.steps[0].add_bc(extra)
    else:
        p.fem.add_bc(extra)
    with pytest.raises(ConflictingBoundaryConditions) as info:
        a.to_fem("d", fem_format, scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    err = info.value
    assert (err.node, err.dof, err.writer) == (tip.id, "DZ", fem_format)
    if other == "settlement":
        assert (err.bc, err.other) == ("tip_extra", "tip_LC_s") and "through another set" in str(err)
    else:
        assert (err.bc, err.other) == ("tip_LC_s", "tip_extra") and "holds it at 0" in str(err)
    assert ("ASSEMBLA_26" if fem_format == "code_aster" else "without a word") in str(err)


@pytest.mark.parametrize("order", [1, 2])
def test_second_order_shells_allow_mumps_an_error_estimate_of_1e_4(tmp_path, order):
    """A thin COQUE_3D (second-order) shell stopped at <FACTOR_57>: MUMPS's error estimate 6.88e-6 over the default
    1e-6, its answer within 1e-8 of the closed form (``static_lin._solver_str``). Second-order shells get
    ``RESI_RELA=1e-4``; first-order ones (DKT, estimate 4.4e-8) keep the default."""
    from ada.fem.concept.constraints import ConstraintConceptCurve
    from ada.fem.meshing import GmshOptions

    pl = ada.Plate("pl", [(0, 0), (L, 0), (L, 0.5), (0, 0.5)], 0.01, mat=ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("P") / pl
    a = ada.Assembly("A") / p
    p.concept_fem.constraints.add_curve_constraint(
        ConstraintConceptCurve("root", (0, 0, 0), (0, 0.5, 0), Dof.encastre())
    )
    q = (0, 0, -500.0)
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC_e", [LoadConceptLine("e", (L, 0, 0), (L, 0.5, 0), q, q)]))
    p.fem = p.to_fem_obj(0.25, use_quads=True, options=GmshOptions(Mesh_ElementOrder=order))
    comm, _ = _comm(a, tmp_path)
    m = re.search(r"^result = MACRO_ELAS_MULT\((.*?)^\)", comm, re.M | re.S)
    solver = "SOLVEUR=_F(METHODE='MUMPS', RESI_RELA=1e-4)," in m[1]
    assert solver == (order == 2) and comm.count("SOLVEUR") == int(order == 2)


@pytest.mark.parametrize("with_beam", [False, True])
def test_stresses_left_out_beside_second_order_shells_name_the_beams_too(tmp_path, with_beam):
    """A model with second-order shells prints DEPL, REAC_NODA and EFGE only (Code_Aster cannot write the shells'
    layered fields to MED, <MED2_20>), and that list is the whole model's: a beam's stresses (SIEF_ELNO, SIPO_ELNO)
    are left out with the shells'. The finding named the shells only; it now names the beams' element sets too."""
    from ada.fem import FieldOutput
    from ada.fem.concept.constraints import ConstraintConceptCurve
    from ada.fem.meshing import GmshOptions

    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("pl", [(0, 0), (L, 0), (L, 0.5), (0, 0.5)], 0.01, mat=mat)
    p = ada.Part("P") / pl
    if with_beam:
        p / ada.Beam("stiff", (0, 0.25, 0), (L, 0.25, 0), "IPE300", mat)
    a = ada.Assembly("A") / p
    p.concept_fem.constraints.add_curve_constraint(
        ConstraintConceptCurve("root", (0, 0, 0), (0, 0.5, 0), Dof.encastre())
    )
    q = (0, 0, -500.0)
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC_e", [LoadConceptLine("e", (L, 0, 0), (L, 0.5, 0), q, q)]))
    p.fem = p.to_fem_obj(0.25, use_quads=True, options=GmshOptions(Mesh_ElementOrder=2))
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    assert (len(p.fem.sections.lines) > 0) == with_beam
    comm, report = _comm(a, tmp_path)
    assert 'NOM_CHAM=("DEPL", "REAC_NODA", "EFGE_ELNO", "EFGE_NOEU",)' in comm
    (found,) = [f for f in report.findings if f.keyword == "IMPR_RESU"]
    assert found.kind == "omitted"
    assert ("beams' stresses (SIEF_ELNO, SIPO_ELNO)" in found.reason) == with_beam
    assert found.details.get("beams") == (p.fem.sections.lines[0].elset.name if with_beam else None)


def test_the_three_writers_write_the_steps_in_one_order(tmp_path):
    """The part's concept step made first, an eigen step added to the assembly after it: every writer puts the
    assembly's steps first, then each part's -- the order the Abaqus writer uses (``abaqus_steps``). adapy keeps no
    creation order across the assembly and its parts, so the eigen step comes first in all three decks (the review's
    probe ran it so in ccx and Code_Aster). Decided: kept, and the CalculiX and Code_Aster writers take the Abaqus
    writer's list, so the three cannot drift apart."""
    from ada.fem import StepEigen

    a, p, _ = _beam()
    a.fem.add_step(StepEigen("eig", num_eigen_modes=3))
    order = {}
    for fmt in ("abaqus", "calculix", "code_aster"):
        a.to_fem(f"o_{fmt}", fmt, scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    abq = (tmp_path / "o_abaqus" / "o_abaqus.inp").read_text()
    order["abaqus"] = re.findall(r"step_(\w+)\.inp", abq)
    ccx = (tmp_path / "o_calculix" / "o_calculix.inp").read_text()
    order["calculix"] = list(dict.fromkeys(re.findall(r"^\*\* STEP: (\w+)", ccx, re.M)))
    comm = (tmp_path / "o_code_aster" / "o_code_aster.comm").read_text()
    marks = {"eig": comm.index("#modal analysis"), "concept_loads": comm.index("# Load cases of step concept_loads")}
    order["code_aster"] = sorted(marks, key=marks.get)
    assert order == {fmt: ["eig", "concept_loads"] for fmt in order}
