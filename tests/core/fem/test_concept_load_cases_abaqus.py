"""Concept load cases through the Abaqus writer: one linear perturbation step, one ``*Load Case`` per case.

The step the concept load cases become lives on the part's FEM, and the Abaqus writer used to write the assembly's
steps only, so a meshed GeniE model came out of it with no step and no load. A step with load cases was also written
as one general step holding every case's loads, which sums them. Measured with Abaqus 2025 on a 4 m B31 beam
(``D:/temp/concept_loads_probe/abq1``): model data's ``*Boundary`` holds in every load case of a perturbation step,
a ``*Boundary`` with a value in a load case prescribes it in that case only, and ``*Dload PZ`` on a B31 equals the
consistent nodal ``*Cload`` qL/2 to all printed digits. Solved, the deck below gave reaction totals 4000, 1600,
8000, 10000, 10000, 1598 (gravity on Abaqus' own I-section area) and 7598 = 1.5 x 4000 + 1598.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

import ada
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
    LoadConceptPoint,
    LoadConceptPrescribedDisplacement,
)
from ada.fem.formats import conversion_report
from ada.materials.metals import CarbonSteel

L = 4.0
CASES = ["LC_u", "LC_part", "LC_lin", "LC_pmid", "LC_poff", "LC_grav", "LC_settle", "LCC"]


def _model():
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    a = ada.Assembly("ss") / p
    c = p.concept_fem.constraints
    pin = [Dof(d, "fixed") for d in ("dx", "dy", "dz", "rx")] + [Dof(d, "free") for d in ("ry", "rz")]
    roll = (
        [Dof(d, "fixed") for d in ("dy",)]
        + [Dof("dz", "prescribed")]
        + [Dof(d, "free") for d in ("dx", "rx", "ry", "rz")]
    )
    c.add_point_constraint(ConstraintConceptPoint("pin", (0, 0, 0), pin))
    sp = c.add_point_constraint(ConstraintConceptPoint("roll", (L, 0, 0), roll))
    q = (0, 0, -1000.0)
    loads = {
        "LC_u": [LoadConceptLine("u", (0, 0, 0), (L, 0, 0), q, q)],
        "LC_part": [LoadConceptLine("part", (1.3, 0, 0), (2.9, 0, 0), q, q)],
        "LC_lin": [LoadConceptLine("lin", (0, 0, 0), (L, 0, 0), (0, 0, -1000.0), (0, 0, -3000.0))],
        "LC_pmid": [LoadConceptPoint("pm", (2.0, 0, 0), (0, 0, -10000.0), (0, 0, 0))],
        "LC_poff": [LoadConceptPoint("po", (1.3, 0, 0), (0, 0, -10000.0), (0, 0, 0))],
        "LC_grav": [LoadConceptAccelerationField("g", (0, 0, -9.80665))],
        "LC_settle": [LoadConceptPrescribedDisplacement("pd", sp, (0, 0, -0.01))],
    }
    cases = {}
    for i, (name, lds) in enumerate(loads.items(), start=1):
        cases[name] = p.concept_fem.loads.add_load_case(LoadConceptCase(name, lds, fem_loadcase_number=i))
    terms = [LoadConceptCaseFactored(cases["LC_u"], 1.5), LoadConceptCaseFactored(cases["LC_grav"], 1.0)]
    p.concept_fem.loads.add_load_case_combination(LoadConceptCaseCombination("LCC", terms))
    return a, p


@pytest.fixture(scope="module")
def deck(tmp_path_factory):
    a, p = _model()
    work = tmp_path_factory.mktemp("abq")
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, "line")
        a.to_fem("ss", "abaqus", scratch_dir=work, overwrite=True)
    return p, (work / "ss" / "ss.inp").read_text(), report


def _cases(inp: str) -> dict[str, str]:
    return {m[1]: m[2] for m in re.finditer(r"^\*Load Case, name=(\S+)\n(.*?)^\*End Load Case", inp, re.M | re.S)}


def _forces(p, body: str) -> tuple[np.ndarray, float]:
    """The case's beam loads summed: total force in z and its moment about x = 0, from ``*Dload P*`` lines (element
    length times intensity, at the element's middle) and ``*Cload`` lines."""
    fz, my = 0.0, 0.0
    for el_id, label, value in re.findall(r"^beam-FEM\.(\d+), (P[XYZ]), (\S+)$", body, re.M):
        el = p.fem.elements.from_id(int(el_id))
        a, b = (np.asarray(n.p) for n in el.nodes[:2])
        f = float(value) * np.linalg.norm(b - a)
        fz += f if label == "PZ" else 0.0
        my += f * (a[0] + b[0]) / 2 if label == "PZ" else 0.0
    for node_id, dof, value in re.findall(r"^ beam-FEM\.(\d+), (\d), (\S+)$", body, re.M):
        if dof == "3":
            fz += float(value)
            my += float(value) * p.fem.nodes.from_id(int(node_id)).x
    return fz, my


def test_the_part_step_is_written_as_a_perturbation_step_with_one_load_case_per_case(deck):
    _, inp, _ = deck
    assert re.search(r"^\*Step, name=concept_loads, nlgeom=NO, perturbation\n\*Static", inp, re.M)
    assert list(_cases(inp)) == CASES
    assert inp.count("*Step") == 1


def test_each_case_carries_its_own_load_and_no_other(deck):
    p, inp, _ = deck
    cases = _cases(inp)
    totals = {name: _forces(p, body) for name, body in cases.items()}
    assert totals["LC_u"] == pytest.approx((-4000.0, -8000.0))
    assert totals["LC_part"] == pytest.approx((-1600.0, -1600.0 * 2.1))
    # 1000 -> 3000 N/m: 8000 N at x = (1000 * 2 + 2000 * 8 / 3) / 8 ... its moment is int q x dx
    assert totals["LC_lin"] == pytest.approx((-8000.0, -(1000 * L**2 / 2 + 500 * L**3 / 3)))
    assert totals["LC_pmid"][0] == pytest.approx(0.0)  # a point load is a *Cload on its node set, below
    assert re.findall(r"^ beam-FEM\.LC_pmid_pm, 3, (\S+)$", cases["LC_pmid"], re.M) == ["-10000.0"]
    assert re.findall(r"^ beam-FEM\.LC_poff_po, 3, (\S+)$", cases["LC_poff"], re.M) == ["-10000.0"]
    assert re.findall(r"GRAV, (\S+), (\S+), (\S+), (\S+)", cases["LC_grav"]) == [("9.80665", "0.0", "0.0", "-1.0")]
    assert totals["LCC"] == pytest.approx((-6000.0, -12000.0))
    assert "GRAV" in cases["LCC"] and "GRAV" not in cases["LC_u"]


def test_a_uniform_load_over_whole_elements_is_a_dload_and_the_rest_consistent_nodal_forces(deck):
    """LC_part (1.3..2.9 m) loads three elements whole and one in part -- the point loads at 1.3 and 2.0 m are mesh
    nodes, so the mesh is 0, 0.433, 0.867, 1.3, 1.65, 2.0, 2.5, 3.0, 3.5, 4.0 -- ``*Dload`` on the three, and the
    stretch 2.5..2.9 of the element 2.5..3.0 as the nodal forces of the linear element, which is what Abaqus does
    with a *Dload on a B31 itself: 400 N, 240 to the node at 2.5 and 160 to the node at 3.0."""
    p, inp, report = deck
    body = _cases(inp)["LC_part"]
    whole = re.findall(r"^beam-FEM\.(\d+), PZ, -1000.0$", body, re.M)
    spans = sorted(tuple(sorted(n.x for n in p.fem.elements.from_id(int(e)).nodes)) for e in whole)
    assert spans == pytest.approx([(1.3, 1.65), (1.65, 2.0), (2.0, 2.5)])
    nodal = {
        round(p.fem.nodes.from_id(int(n)).x, 6): float(v)
        for n, _, v in re.findall(r"^ beam-FEM\.(\d+), (3), (\S+)$", body, re.M)
    }
    assert nodal == pytest.approx({2.5: -240.0, 3.0: -160.0})
    notes = [f for f in report.findings if f.keyword == "LoadLine"]
    assert {f.subject for f in notes} | {s for f in notes for s in f.other_subjects} == {
        "LC_part_part",
        "LC_lin_lin",
    }


def test_a_settlement_is_valued_in_its_own_load_case_only(deck):
    p, inp, _ = deck
    cases = _cases(inp)
    settled = {name: re.findall(r"^ (\S+), 3, 3, (\S+)$", body, re.M) for name, body in cases.items()}
    assert settled["LC_settle"] == [("beam-FEM.roll_set", "-0.01")]
    assert all(v == [] for name, v in settled.items() if name != "LC_settle")
    # held at zero once in model data
    model = inp.split("*Step")[0]
    assert re.findall(r"^ beam-FEM\.roll_set, 3, 3(.*)$", model, re.M) == [""]


def test_a_step_of_load_cases_with_a_load_in_none_of_them_says_so(tmp_path):
    from ada.fem import FemSet, Load, LoadCase
    from ada.fem.steps import StepImplicitStatic

    a, p = _model()
    p.fem = p.to_fem_obj(0.5, "line")
    (step,) = p.fem.steps
    stray = Load(
        "stray", Load.TYPES.FORCE, 1.0, p.fem.add_set(FemSet("s", [p.fem.nodes[0]], "nset")), dof=[0, 0, 1, 0, 0, 0]
    )
    step.add_load(stray)
    step.add_loadcase(LoadCase("extra", None, loads=[]))
    a.fem.add_step(StepImplicitStatic("other"))
    with conversion_report.collect() as report:
        a.to_fem("stray", "abaqus", scratch_dir=tmp_path, overwrite=True)
    (f,) = [f for f in report.findings if f.subject == "stray"]
    assert f.kind == "omitted"
