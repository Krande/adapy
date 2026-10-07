"""The Calculix deck's beams, steps and loads, by their text (``tests/fem/test_calculix_code_aster_solve.py`` solves).

* A two-node beam is a ``U1`` general section ``A, Iy, 0, Iz, 1e8`` with local z: the fifth value is U1's shear
  coefficient, not the torsion constant, and the first inertia carries bending along the direction given (ccx 2.23,
  measured; the deck before deflected 11.79 m under 1 kN).
* Every step is written, a load case as a ``*STEP`` of its own opening with ``OP=NEW``.
* A point load is a ``*CLOAD`` on its node set; gravity on U1 beams is nodal loads.
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem import LoadGravity, LoadPoint, StepImplicitStatic
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import LoadConceptCase, LoadConceptLine, LoadConceptPoint
from ada.fem.formats import conversion_report
from ada.fem.formats.calculix.write.writer import U1_SHEAR_COEFFICIENT
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
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    return a, p, bm


def _deck(a, tmp_path, name="d"):
    with conversion_report.collect() as report:
        a.to_fem(name, "calculix", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.inp").read_text(), report


def test_a_two_node_beam_is_a_u1_general_section_with_its_shear_coefficient_and_local_z(tmp_path):
    a, p, bm = _beam()
    inp, report = _deck(a, tmp_path)
    assert "*ELEMENT, type=U1, ELSET=elbm_set_bm" in inp
    head, values, direction = re.search(r"(\*Beam Section, [^\n]*section=GENERAL)\n([^\n]*)\n([^\n]*)", inp).groups()
    props = bm.section.properties
    a_, i1, i12, i2, kappa = (float(v) for v in values.split(","))
    assert (a_, i1, i12, i2) == pytest.approx((props.Ax, props.Iy, 0.0, props.Iz))
    assert kappa == U1_SHEAR_COEFFICIENT == 1e8, "U1's fifth value is its shear coefficient"
    assert [float(v) for v in direction.split(",")] == pytest.approx([0.0, 0.0, 1.0]), "Iy bends along local z"
    torsion = [f for f in report.findings if f.keyword == "*BEAM SECTION" and f.kind == "approximated"]
    assert torsion and torsion[0].details["ratio"] == pytest.approx((props.Iy + props.Iz) / props.Ix)


def test_every_step_and_every_load_case_is_a_step_of_its_own(tmp_path):
    a, p, _ = _beam()
    s1 = a.fem.add_step(StepImplicitStatic("first"))
    s1.add_load(LoadPoint("p1", -100.0, p.fem.nsets["LC_p_pm"], 3))
    s2 = a.fem.add_step(StepImplicitStatic("second"))
    s2.add_load(LoadPoint("p2", -200.0, p.fem.nsets["LC_p_pm"], 2))
    inp, _ = _deck(a, tmp_path)
    steps = re.findall(r"^\*\* STEP: ([^\n]*)$", inp, re.M)
    assert steps == ["first", "second", "concept_loads  LOAD CASE: LC_u", "concept_loads  LOAD CASE: LC_p"]
    assert inp.count("*Step,") == 4 and inp.count("*End Step") == 4
    blocks = inp.split("** STEP: ")[1:]
    assert all("*Cload, OP=NEW\n*Dload, OP=NEW" in b for b in blocks), "each static step starts from no load"
    # the second general step carries the first's load, re-listed after OP=NEW
    assert "LC_p_pm, 3, -1.0000000000000E+02" in blocks[1] and "LC_p_pm, 2, -2.0000000000000E+02" in blocks[1]
    assert "LC_p_pm, 2," not in blocks[0]
    # a load case holds its own loads only
    assert "LC_p_pm" not in blocks[2] and "LC_p_pm, 3, -1.0000000000000E+04" in blocks[3]


def test_a_point_load_is_a_cload_on_its_node_set(tmp_path):
    a, p, _ = _beam(cases=False)
    fs = p.fem.add_set(ada.fem.FemSet("tip", [p.fem.nodes.get_by_volume((L, 0, 0))[0]], "nset"))
    a.fem.add_step(StepImplicitStatic("s")).add_load(
        ada.fem.Load("pt", "force", 1.0, fem_set=fs, dof=[10.0, 0.0, -20.0, 0.0, 5.0, 0.0])
    )
    inp, _ = _deck(a, tmp_path)
    block = inp.split("Type: Concentrated force\n*Cload\n")[1].split("\n**")[0]
    assert block.splitlines() == [
        "tip, 1, 1.0000000000000E+01",
        "tip, 3, -2.0000000000000E+01",
        "tip, 5, 5.0000000000000E+00",
    ]


def test_gravity_on_u1_beams_is_their_weight_as_nodal_loads(tmp_path):
    """ccx takes no body force on a U1 element; the weight rho A g goes in as Hermite-consistent nodal loads, summing
    to the beam's weight, and no GRAV names a set with a U1 element in it."""
    a, p, bm = _beam(cases=False)
    a.fem.add_step(StepImplicitStatic("g")).add_load(LoadGravity("grav", -9.81))
    inp, _ = _deck(a, tmp_path)
    loads = inp.split("Type: Gravity\n")[1].split("\n**")[0]
    assert "GRAV" not in loads
    rows = re.findall(r"^(\d+), (\d), (\S+)$", loads, re.M)
    fz = sum(float(v) for _, dof, v in rows if dof == "3")
    props = bm.section.properties
    assert fz == pytest.approx(-bm.material.model.rho * props.Ax * 9.81 * L, rel=1e-12)


def test_a_line_load_on_u1_beams_has_the_hermite_end_moments(tmp_path):
    """Uniform 1000 N/m down on 0.5 m elements: 250 N at each end and +- q h^2 / 12 = 20.83 N m about y there --
    ``M1 = int N2 (t x q) dx`` with ``t x q = (0, +1000, 0)``, so + at the near end."""
    a, p, _ = _beam()
    inp, _ = _deck(a, tmp_path)
    block = inp.split("LOAD CASE: LC_u")[1].split("*End Step")[0]
    rows = {(int(n), int(d)): float(v) for n, d, v in re.findall(r"^(\d+), (\d), (\S+)$", block, re.M)}
    end_a = p.fem.nodes.get_by_volume((0, 0, 0))[0].id
    end_b = p.fem.nodes.get_by_volume((L, 0, 0))[0].id
    assert rows[(end_a, 3)] == pytest.approx(-250.0) and rows[(end_b, 3)] == pytest.approx(-250.0)
    assert rows[(end_a, 5)] == pytest.approx(1000.0 * 0.5**2 / 12)
    assert rows[(end_b, 5)] == pytest.approx(-1000.0 * 0.5**2 / 12)


def test_a_prescribed_displacement_takes_its_value_in_its_own_step_and_zero_in_the_others(tmp_path):
    """Each load case step gives every prescribed dof the value of its case, or zero (ccx carries a ``*BOUNDARY``
    value into the next step); model data holds the dof. The values were not written at all."""
    from ada.fem import Bc, FemSet, LoadCase
    from ada.fem.constraints import BC_LOAD_CASE

    pl = ada.Plate("pl", [(0, 0), (L, 0), (L, 0.5), (0, 0.5)], 0.01, mat=ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("Pl") / pl
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, use_quads=True)
    root = p.fem.add_set(FemSet("root", [n for n in p.fem.nodes if abs(n.x) < 1e-9], FemSet.TYPES.NSET))
    tip = p.fem.add_set(FemSet("tip", [n for n in p.fem.nodes if abs(n.x - L) < 1e-9], FemSet.TYPES.NSET))
    p.fem.add_bc(Bc("fix", root, [1, 2, 3, 4, 5, 6]))
    p.fem.add_bc(Bc("tip_hold", tip, [3]))
    step = p.fem.add_step(StepImplicitStatic("settle"))
    for name, v in {"LC1": -0.01, "LC2": 0.02}.items():
        p.fem.add_bc(Bc(f"tip_{name}", tip, [3], magnitudes=[v], metadata={BC_LOAD_CASE: name}))
        step.add_loadcase(LoadCase(name, None, loads=[]))
    step.add_loadcase(LoadCase("LC_none", None, loads=[]))
    inp, report = _deck(a, tmp_path)
    assert not report.of_kind("omitted"), report.summary()
    model_data, *steps = inp.split("*Step")
    assert re.findall(r"^ tip, 3$", model_data, re.M), "model data holds the dof"
    assert [re.findall(r"^ tip, 3, 3, (\S+)$", s, re.M) for s in steps] == [
        ["-1.0000000000000E-02"],
        ["2.0000000000000E-02"],
        ["0.0000000000000E+00"],
    ], "a case naming no settlement puts the dof back at zero: ccx carries a *BOUNDARY value into the next step"


def test_a_prescribed_displacement_on_a_u1_beam_is_held_at_zero_and_named(tmp_path):
    """ccx 2.23 does not solve a prescribed displacement on a U1 beam (measured: a prescribed rigid translation bent
    the beam to -0.1575 m at mid-span), so the value is not written and each settlement is reported ``omitted``."""
    from ada.fem.concept.loads import LoadConceptPrescribedDisplacement

    a, p, _ = _beam(cases=False)
    tip = [Dof(d, "free") for d in ("dx", "dy", "rx", "ry", "rz")] + [Dof("dz", "prescribed")]
    sp = p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("tip", (L / 2, 0, 0), tip))
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC_s", [LoadConceptPrescribedDisplacement("pd", sp, (0, 0, -1e-2))])
    )
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    inp, report = _deck(a, tmp_path)
    (found,) = [f for f in report.of_kind("omitted") if f.keyword == "*BOUNDARY"]
    assert found.subject == "tip_LC_s" and "U1 beam" in found.reason
    assert not re.search(r"^ tip_set, 3, 3, ", inp, re.M)
