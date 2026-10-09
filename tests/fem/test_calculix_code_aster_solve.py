"""Calculix and Code_Aster solve adapy's decks, against closed forms and against Sestra on the same models.

The models are those ``test_concept_loads_sestra`` verifies Sestra on: concept loads and supports, meshed by
``Part.to_fem_obj`` and written by the writers, no step added by hand -- the concept load cases are one step of load
cases. Each solver's leg skips when the solver is not installed.

* An IPE300 beam, L = 4 m in 0.5 m elements, simply supported: a uniform, a partial (1.3..2.9 m) and a linear line load,
  a point load at mid-span and one at 1.3 m (a node meshing made), gravity and the combination 1.5 x uniform + gravity,
  seven load cases in one step. CalculiX writes the beam as U1 elements and Code_Aster as POU_D_E, both
  Euler-Bernoulli, and both take a line load as the element's Hermite-consistent nodal loads where it is not uniform
  over a whole element: so every case's mid-span deflection is the Euler-Bernoulli closed form, exactly. Sestra's beam
  carries shear deformation as well (G Sharz); where Sestra is installed its deflection is Code_Aster's plus that
  shear term, case by case.
* The same beam cantilevered under a tip force and a tip moment, an acceleration along y, a general step with two
  loads, and two general steps (the second carrying the first's load, as Abaqus and CalculiX have it).
* A 4.0 x 0.5 m, 10 mm plate strip in cylindrical bending under 1000 Pa and under gravity, and the same plate
  cantilevered under a tip edge load.

What each measured (CalculiX 2.23, Code_Aster 18.1.8, Sestra V11.3-00) is in the tests and in
``CALCULIX_CODE_ASTER.md``. CalculiX's .frd prints six significant digits (``%12.5E``), which is the tolerance
of every CalculiX comparison with an exact answer; Code_Aster's MED file carries doubles.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import FieldOutput, StepImplicitStatic
from ada.fem.concept.constraints import ConstraintConceptCurve
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
    LoadConceptSurface,
)
from ada.materials.metals import CarbonSteel

L, G_ACC = 4.0, 9.80665
WID, T = 0.5, 0.010
SOLVERS = ("calculix", "code_aster")
#: ccx's .frd prints ``%12.5E``: half a unit in the sixth significant digit.
FRD = 5e-6


def _dofs(fixed):
    return [Dof(d, "fixed" if d in fixed else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]


def _ipe300_beam(name="bm"):
    bm = ada.Beam(name, (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    return ada.Assembly("ss") / p, p, bm


class Solved:
    """One deck solved: the result read back, and lookups by position and case."""

    def __init__(self, fem_format, res, dat=None):
        self.fem_format = fem_format
        self.res = res
        self.dat = dat
        self.coords = {
            int(i): np.asarray(c, dtype=float) for i, c in zip(res.mesh.nodes.identifiers, res.mesh.nodes.coords)
        }

    def node(self, x, y=0.0, z=None):
        ids = list(self.coords)
        dist = [np.hypot(self.coords[i][0] - x, self.coords[i][1] - y) for i in ids]
        best = ids[int(np.argmin(dist))]
        assert min(dist) < 1e-6, f"no node at ({x}, {y})"
        return best

    def _fields(self, kind, case, result="result"):
        out = []
        for f in self.res.results:
            name = f.name
            if self.fem_format == "calculix":
                ok = name.startswith({"u": "DISP", "rf": "FORC"}[kind]) and f.step == case
            else:
                # the MED field name: the result concept's name padded to 8 characters with "_", then the field's
                ok = name.split("[")[0] == result.ljust(8, "_") + {"u": "DEPL", "rf": "REAC_NODA"}[kind] and (
                    f.step == case or "[" not in name
                )
            if ok:
                out.append(f)
        assert len(out) == 1, f"{kind} of case {case}: {[f.name for f in out]}"
        return {int(row[0]): np.asarray(row[1:], dtype=float) for row in np.asarray(out[0].values)}

    def u(self, case, node, result="result"):
        return self._fields("u", case, result)[node]

    def reactions(self, case, nodes=None, result="result"):
        rf = self._fields("rf", case, result)
        nodes = rf.keys() if nodes is None else nodes
        force = sum((rf[n][:3] for n in nodes), np.zeros(3))
        moment = sum(
            (np.cross(self.coords[n], rf[n][:3]) + (rf[n][3:6] if len(rf[n]) >= 6 else 0) for n in nodes), np.zeros(3)
        )
        return force, moment


def _solve(a, name, fem_format, tmp_path) -> Solved:
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.utils import default_fem_res_path

    a.to_fem(name, fem_format, scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    res_path = default_fem_res_path(name, scratch_dir=tmp_path, fem_format=FEATypes.from_str(fem_format))
    if fem_format == "code_aster":
        # A run that stops part-way leaves a MED file holding what was printed before it stopped -- displacements and
        # reactions, say -- and a test reading those passes on a failed run (second-order shells: <MED2_20> at the
        # stresses, measured). Code_Aster's own verdict decides: an alarm is a run, an error is not.
        mess = (tmp_path / name / f"{name}.mess").read_text(encoding="utf-8", errors="replace")
        (verdict,) = [line for line in mess.splitlines() if "DIAGNOSTIC JOB" in line]
        assert "<S>" not in verdict and "<F>" not in verdict and "<E>" not in verdict, verdict
    return Solved(fem_format, ada.from_fem_res(res_path), res_path.with_suffix(".dat"))


# --- the simply supported beam, seven load cases in one step ----------------------------------------------------

BEAM_CASES = ["LC_u", "LC_part", "LC_lin", "LC_pmid", "LC_poff", "LC_grav", "LCC"]


def _ss_beam():
    a, p, bm = _ipe300_beam()
    c = p.concept_fem.constraints
    c.add_point_constraint(ConstraintConceptPoint("pin", (0, 0, 0), _dofs(("dx", "dy", "dz", "rx"))))
    c.add_point_constraint(ConstraintConceptPoint("roll", (L, 0, 0), _dofs(("dy", "dz"))))
    q = (0, 0, -1000.0)
    loads = {
        "LC_u": [LoadConceptLine("u", (0, 0, 0), (L, 0, 0), q, q)],
        "LC_part": [LoadConceptLine("part", (1.3, 0, 0), (2.9, 0, 0), q, q)],
        "LC_lin": [LoadConceptLine("lin", (0, 0, 0), (L, 0, 0), (0, 0, -1000.0), (0, 0, -3000.0))],
        "LC_pmid": [LoadConceptPoint("pm", (2.0, 0, 0), (0, 0, -10000.0), (0, 0, 0))],
        "LC_poff": [LoadConceptPoint("po", (1.3, 0, 0), (0, 0, -10000.0), (0, 0, 0))],
        "LC_grav": [LoadConceptAccelerationField("g", (0, 0, -G_ACC))],
    }
    cases = {}
    for i, (name, lds) in enumerate(loads.items(), start=1):
        cases[name] = p.concept_fem.loads.add_load_case(LoadConceptCase(name, lds, fem_loadcase_number=i))
    terms = [LoadConceptCaseFactored(cases["LC_u"], 1.5), LoadConceptCaseFactored(cases["LC_grav"], 1.0)]
    p.concept_fem.loads.add_load_case_combination(LoadConceptCaseCombination("LCC", terms))
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, p, bm


@pytest.fixture(scope="module", params=SOLVERS)
def ss_beam(request, require_solver, tmp_path_factory):
    require_solver(request.param)
    a, p, bm = _ss_beam()
    return _solve(a, "ss", request.param, tmp_path_factory.mktemp(f"ss_{request.param}")), bm


def _closed_forms(bm, shear=False) -> dict[str, tuple[float, float, float]]:
    """``{case: (mid-span deflection downwards, reaction at x = L, total load)}``: Euler-Bernoulli, and with the shear
    term Sestra's beam adds (``G Sharz``, adapy's section) when ``shear``. Integrated exactly over the load."""
    props, mat = bm.section.properties, bm.material.model
    ei, gas = mat.E * props.Iy, mat.E / (2 * (1 + mat.v)) * props.Sharz
    weight = mat.rho * props.Ax * G_ACC

    def influence(x, xi):
        if x <= xi:
            b = L - xi
            return b * x * (L**2 - b**2 - x**2) / (6 * ei * L) + (b * x / (gas * L) if shear else 0.0)
        return influence(L - x, L - xi)

    def distributed(q, a0, a1, x=L / 2):
        g, w = np.polynomial.legendre.leggauss(6)
        cuts = sorted({a0, a1, min(max(x, a0), a1)})
        defl, rb, total = 0.0, 0.0, 0.0
        for lo, hi in zip(cuts[:-1], cuts[1:]):
            for xk, wk in zip(0.5 * (hi - lo) * g + 0.5 * (hi + lo), 0.5 * (hi - lo) * w):
                defl += wk * q(xk) * influence(x, xk)
                rb += wk * q(xk) * xk / L
                total += wk * q(xk)
        return defl, rb, total

    out = {
        "LC_u": distributed(lambda s: 1000.0, 0, L),
        "LC_part": distributed(lambda s: 1000.0, 1.3, 2.9),
        "LC_lin": distributed(lambda s: 1000.0 + 500.0 * s, 0, L),
        "LC_pmid": (10000.0 * influence(2.0, 2.0), 5000.0, 10000.0),
        "LC_poff": (10000.0 * influence(2.0, 1.3), 10000.0 * 1.3 / L, 10000.0),
        "LC_grav": distributed(lambda s: weight, 0, L),
    }
    out["LCC"] = tuple(1.5 * u + g for u, g in zip(out["LC_u"], out["LC_grav"]))
    return out


@pytest.mark.parametrize("case", BEAM_CASES)
def test_the_beam_deflects_as_euler_bernoulli(ss_beam, case):
    """Mid-span deflection, every case, against Euler-Bernoulli.

    Measured: CalculiX 1.98438e-4 m under the uniform load (closed form 1.984378e-4) and 7.93751e-4 m under the point
    load; Code_Aster 1.9843782693e-4 and 7.9375130774e-4. Before the fix CalculiX's beam deflected 11.79 m under 1 kN:
    adapy wrote the torsion constant where U1 takes its shear coefficient, and the strong inertia across the vertical.
    """
    solved, bm = ss_beam
    number = BEAM_CASES.index(case) + 1
    w, _, _ = _closed_forms(bm)[case]
    uz = solved.u(number, solved.node(L / 2))[2]
    tol = FRD if solved.fem_format == "calculix" else 1e-9
    assert -uz == pytest.approx(w, rel=tol)


@pytest.mark.parametrize("case", BEAM_CASES)
def test_code_aster_reacts_as_the_closed_form(ss_beam, case):
    """The reaction at x = L and the total, every case: statics. (CalculiX's nodal forces at U1 nodes are the elements'
    end forces, not reactions -- measured +500 and -500 N for two 500 N reactions -- so CalculiX's beam reactions are
    not asserted; its shell reactions are, below.)"""
    solved, bm = ss_beam
    if solved.fem_format != "code_aster":
        pytest.skip("CalculiX writes no reactions at U1 nodes")
    number = BEAM_CASES.index(case) + 1
    _, rb, total = _closed_forms(bm)[case]
    assert solved.reactions(number)[0][2] == pytest.approx(total, rel=1e-9)
    assert solved.reactions(number, [solved.node(L)])[0][2] == pytest.approx(rb, rel=1e-9)


@pytest.fixture(scope="module")
def sestra_beam(tmp_path_factory):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        if get_sestra_default_exe_path() is None:
            pytest.skip("Sestra is not installed")
    except Exception:  # noqa: BLE001 - any locator failure is "not installed"
        pytest.skip("Sestra is not installed")
    a, p, bm = _ss_beam()
    tmp = tmp_path_factory.mktemp("ss_sestra")
    a.to_fem("ss", "sesam", scratch_dir=tmp, overwrite=True, execute=True)
    res = read_sin_file(tmp / "ss" / "ssR1.SIN")
    ids = list(res.mesh.nodes.identifiers)
    (mid,) = [n.id for n in p.fem.nodes if abs(n.x - L / 2) < 1e-9]
    return {
        case: float(
            [np.asarray(r.values) for r in res.results if r.name == "sesam.nodes.displacement" and r.step == k][0][
                ids.index(mid)
            ][4]
        )
        for k, case in enumerate(BEAM_CASES, start=1)
    }, bm


@pytest.mark.parametrize("case", BEAM_CASES)
def test_sestra_is_code_aster_plus_its_shear_term(ss_beam, sestra_beam, case):
    """The three solvers on one adapy model: Sestra's mid-span deflection less the shear term its beam carries is the
    Euler-Bernoulli deflection Code_Aster and CalculiX give. Sestra's SIN file is single precision (1e-6)."""
    solved, bm = ss_beam
    sestra, _ = sestra_beam
    number = BEAM_CASES.index(case) + 1
    eb = _closed_forms(bm)[case][0]
    shear = _closed_forms(bm, shear=True)[case][0] - eb
    other = -solved.u(number, solved.node(L / 2))[2]
    assert -sestra[case] - shear == pytest.approx(other, rel=FRD if solved.fem_format == "calculix" else 1e-6)


# --- the cantilever, an acceleration along y, two loads in one step, two steps --------------------------------------


def _cantilever():
    a, p, bm = _ipe300_beam()
    p.concept_fem.constraints.add_point_constraint(
        ConstraintConceptPoint("fix", (0, 0, 0), _dofs(("dx", "dy", "dz", "rx", "ry", "rz")))
    )
    ld = p.concept_fem.loads
    ld.add_load_case(LoadConceptCase("LC_F", [LoadConceptPoint("F", (L, 0, 0), (0, 0, -1000.0), (0, 0, 0))]))
    ld.add_load_case(LoadConceptCase("LC_M", [LoadConceptPoint("M", (L, 0, 0), (0, 0, 0), (0, 1000.0, 0))]))
    ld.add_load_case(LoadConceptCase("LC_gy", [LoadConceptAccelerationField("gy", (0, -G_ACC, 0))]))
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, p, bm


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_cantilever_takes_its_tip_force_tip_moment_and_lateral_acceleration(fem_format, require_solver, tmp_path):
    """Tip force P: w = -P L^3 / (3 EI), rotation about y = P L^2 / (2 EI). Tip moment M about +y: w = -M L^2 / (2 EI),
    rotation M L / EI. An acceleration of g along -y: v = -q L^4 / (8 E Iz), q = rho A g -- which Code_Aster applied along
    z before (PESANTEUR's direction was always (0, 0, 1)), and which CalculiX could not take on a U1 beam at all
    (ccx 2.23: "*ERROR in e_c3d_u1: no body forces"; its weight is written as nodal loads)."""
    require_solver(fem_format)
    a, p, bm = _cantilever()
    solved = _solve(a, "cant", fem_format, tmp_path)
    props, mat = bm.section.properties, bm.material.model
    ei, eiz, q = mat.E * props.Iy, mat.E * props.Iz, mat.rho * props.Ax * G_ACC
    tip = solved.node(L)
    tol = FRD if fem_format == "calculix" else 1e-9
    u_f, u_m, u_g = (solved.u(k, tip) for k in (1, 2, 3))
    assert u_f[2] == pytest.approx(-1000.0 * L**3 / (3 * ei), rel=tol)
    assert u_f[4] == pytest.approx(1000.0 * L**2 / (2 * ei), rel=tol)
    assert u_m[2] == pytest.approx(-1000.0 * L**2 / (2 * ei), rel=tol)
    assert u_m[4] == pytest.approx(1000.0 * L / ei, rel=tol)
    assert u_g[1] == pytest.approx(-q * L**4 / (8 * eiz), rel=tol)
    assert abs(u_g[2]) < 1e-12
    if fem_format == "code_aster":
        force, moment = solved.reactions(1)
        assert force == pytest.approx((0, 0, 1000.0), abs=1e-9 * 1000)
        assert moment == pytest.approx((0, -1000.0 * L, 0), abs=1e-9 * 1000 * L)


def test_a_code_aster_step_of_one_load_case_reads_back_as_case_1(require_solver, tmp_path):
    """The clamped beam with one concept load case, a 1 kN tip force: one ``MACRO_ELAS_MULT`` case. Its fields carry
    ``PDT = 999.999`` and ``NDT = 1`` (measured, 18.1.8), and the reader took the order number only for two cases or
    more, so this one read back as step 999.999. Now step 1, as case 1 of several does; the tip -P L^3 / (3 EI)."""
    require_solver("code_aster")
    a, p, bm = _ipe300_beam()
    p.concept_fem.constraints.add_point_constraint(
        ConstraintConceptPoint("fix", (0, 0, 0), _dofs(("dx", "dy", "dz", "rx", "ry", "rz")))
    )
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC_F", [LoadConceptPoint("F", (L, 0, 0), (0, 0, -1000.0), (0, 0, 0))])
    )
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, "one", "code_aster", tmp_path)
    assert {float(f.step) for f in solved.res.results} == {1.0}
    ei = bm.material.model.E * bm.section.properties.Iy
    assert solved.u(1, solved.node(L))[2] == pytest.approx(-1000.0 * L**3 / (3 * ei), rel=1e-9)


def _two_load_beam():
    """The simply supported beam with its concept step set aside: its uniform and mid-span point loads to hand."""
    a, p, bm = _ss_beam()
    (step,) = p.fem.steps
    loads = {name: lc.loads[0] for name, lc in step.load_cases.items()}
    p.fem.steps.remove(step)
    return a, p, bm, loads


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_step_with_two_loads_applies_both(fem_format, require_solver, tmp_path):
    """One general step, a uniform line load and a mid-span point load: the deflection is the sum. Code_Aster's step
    named its first load only under EXCIT (``step.loads[0]``); CalculiX raised on the point load."""
    require_solver(fem_format)
    a, p, bm, loads = _two_load_beam()
    step = a.fem.add_step(StepImplicitStatic("both"))
    step.add_load(loads["LC_u"])
    step.add_load(loads["LC_pmid"])
    solved = _solve(a, "both", fem_format, tmp_path)
    cf = _closed_forms(bm)
    w = cf["LC_u"][0] + cf["LC_pmid"][0]
    assert -solved.u(1, solved.node(L / 2))[2] == pytest.approx(w, rel=FRD if fem_format == "calculix" else 1e-9)


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_every_step_is_written_and_the_second_carries_the_first(fem_format, require_solver, tmp_path):
    """Step 1 a mid-span point load, step 2 the uniform load: step 2 deflects under both, as Abaqus and CalculiX have it
    (a load carries into the next step; measured with ccx 2.23). CalculiX wrote the first step only."""
    require_solver(fem_format)
    a, p, bm, loads = _two_load_beam()
    a.fem.add_step(StepImplicitStatic("s1")).add_load(loads["LC_pmid"])
    a.fem.add_step(StepImplicitStatic("s2")).add_load(loads["LC_u"])
    solved = _solve(a, "steps", fem_format, tmp_path)
    cf = _closed_forms(bm)
    tol = FRD if fem_format == "calculix" else 1e-9
    mid = solved.node(L / 2)
    if fem_format == "calculix":
        w1, w2 = -solved.u(1, mid)[2], -solved.u(2, mid)[2]
    else:
        w1, w2 = -solved.u(None, mid, "result")[2], -solved.u(None, mid, "result2")[2]
    assert w1 == pytest.approx(cf["LC_pmid"][0], rel=tol)
    assert w2 == pytest.approx(cf["LC_pmid"][0] + cf["LC_u"][0], rel=tol)


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_step_support_holds_in_its_step_and_the_next(fem_format, require_solver, tmp_path):
    """The cantilever's root held in dx, dy, dz by the model and in rx, ry, rz by step 1 (``Step.add_bc``: where the
    Abaqus reader puts a ``*Boundary`` found inside a ``*Step``); step 1 a 1 kN tip force, step 2 a second 1 kN that
    carries the first. Both steps are the clamped cantilever: ``w = -P L^3 / (3 EI)``, then twice that.

    Measured: CalculiX wrote the step's ``*Boundary`` in its step and ccx carried it into step 2, tip -1.27000e-3 m
    (the closed form -1.2700021e-3 to the six digits printed); Code_Aster left the step's support out of the deck
    without a word and stopped at <FACTOR_11> (a mechanism). Now each of its steps lists the supports in force:
    -1.2700020923796e-3 m (+4.6e-13).
    """
    from ada.fem import Bc, FemSet, Load

    require_solver(fem_format)
    a, p, bm = _ipe300_beam()
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    root = p.fem.add_set(FemSet("root", [n for n in p.fem.nodes if abs(n.x) < 1e-9], FemSet.TYPES.NSET))
    tip = p.fem.add_set(FemSet("tipn", [n for n in p.fem.nodes if abs(n.x - L) < 1e-9], FemSet.TYPES.NSET))
    p.fem.add_bc(Bc("fem_fix", root, [1, 2, 3]))
    s1 = a.fem.add_step(StepImplicitStatic("s1"))
    s1.add_bc(Bc("step_fix", root, [4, 5, 6]))
    s1.add_load(Load("F1", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=tip))
    s2 = a.fem.add_step(StepImplicitStatic("s2"))
    s2.add_load(Load("F2", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=tip))
    for s in (s1, s2):
        s.add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, "stepbc", fem_format, tmp_path)
    ei = bm.material.model.E * bm.section.properties.Iy
    tol = FRD if fem_format == "calculix" else 1e-9
    node = solved.node(L)
    if fem_format == "calculix":
        w1, w2 = solved.u(1, node)[2], solved.u(2, node)[2]
    else:
        w1, w2 = solved.u(None, node, "result")[2], solved.u(None, node, "result2")[2]
    assert w1 == pytest.approx(-1000.0 * L**3 / (3 * ei), rel=tol)
    assert w2 == pytest.approx(-2000.0 * L**3 / (3 * ei), rel=tol)


# --- plates ------------------------------------------------------------------------------------------------------


def _plate(name):
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate(name, [(0, 0), (L, 0), (L, WID), (0, WID)], T, mat=mat)
    p = ada.Part(name.capitalize()) / pl
    return ada.Assembly(f"{name}_a") / p, p, pl, mat


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_strip_under_pressure_and_gravity(fem_format, require_solver, tmp_path):
    """5 q L^4 / (384 D) under 1000 Pa and under its weight, quads at 0.125 m.

    Measured: Code_Aster (DKT) -0.17319791669 m under the pressure -- Sestra's -0.17319791019 on the same mesh, the
    consistent-load answer 0.078 % short of the closed form -- and -0.13333157 m under gravity (-0.078 %); CalculiX
    (S4, a C3D8I layer) -0.173065 and -0.133229 m, both -0.155 %. Code_Aster's reactions are q A to 1e-9; CalculiX's
    nodal force at a support node is the reaction less the load applied there, so its total is not asserted here.
    """
    require_solver(fem_format)
    a, p, pl, mat = _plate("strip")
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dz"))))
    c.add_curve_constraint(ConstraintConceptCurve("x4", (L, 0, 0), (L, WID, 0), _dofs(("dz",))))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    loads = p.concept_fem.loads
    loads.add_load_case(LoadConceptCase("LC_p", [LoadConceptSurface("P", pl, pressure=1000.0, side="front")]))
    loads.add_load_case(LoadConceptCase("LC_g", [LoadConceptAccelerationField("G", (0, 0, -G_ACC))]))
    p.fem = p.to_fem_obj(0.125, use_quads=True)
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, "strip", fem_format, tmp_path)

    d = mat.model.E * T**3 / (12 * (1 - mat.model.v**2))
    q_g = mat.model.rho * T * G_ACC
    mid = solved.node(L / 2)
    w_p, w_g = solved.u(1, mid)[2], solved.u(2, mid)[2]
    closed_p, closed_g = -5 * 1000.0 * L**4 / (384 * d), -5 * q_g * L**4 / (384 * d)
    if fem_format == "code_aster":
        assert w_p == pytest.approx(-0.1731979101896286, rel=1e-6), "Sestra's consistent-load answer on this mesh"
        assert w_g / closed_g - 1 == pytest.approx(-7.8e-4, abs=1e-5)
        assert solved.reactions(1)[0][2] == pytest.approx(1000.0 * L * WID, rel=1e-9)
        assert solved.reactions(2)[0][2] == pytest.approx(q_g * L * WID, rel=1e-9)
    else:
        assert w_p / closed_p - 1 == pytest.approx(-1.55e-3, abs=2e-5)
        assert w_g / closed_g - 1 == pytest.approx(-1.55e-3, abs=2e-5)


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_cantilever_plate_takes_its_tip_edge_load(fem_format, require_solver, tmp_path):
    """500 N/m along the tip edge of the cantilevered plate: the root reacts 250 N and -1000 N m about y.

    No load sits on a root node, so CalculiX's nodal forces there are the reactions (249.996 N as printed). The tip
    deflects -0.6018318 m in Code_Aster (DKT), -0.6020137 m in Sestra and -0.593815 m in CalculiX (S4), whose single
    brick layer is 1.3 % stiffer at this mesh (-0.60026 m at 0.0625 m).
    """
    require_solver(fem_format)
    a, p, pl, _ = _plate("cant")
    p.concept_fem.constraints.add_curve_constraint(
        ConstraintConceptCurve("root", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dy", "dz", "rx", "ry", "rz")))
    )
    q = (0, 0, -500.0)
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC_eu", [LoadConceptLine("EU", (L, 0, 0), (L, WID, 0), q, q)]))
    p.fem = p.to_fem_obj(0.125, use_quads=True)
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, "cplate", fem_format, tmp_path)

    root = [n for n, c in solved.coords.items() if abs(c[0]) < 1e-9]
    force, moment = solved.reactions(1, root)
    tol = 1e-4 if fem_format == "calculix" else 1e-9
    assert force[2] == pytest.approx(250.0, rel=tol)
    tip = solved.u(1, solved.node(L))[2]
    if fem_format == "code_aster":
        assert moment[1] == pytest.approx(-250.0 * L, rel=1e-9)
        assert tip == pytest.approx(-0.6020137, rel=4e-4), "Sestra on the same mesh"
    else:
        assert tip == pytest.approx(-0.6018318, rel=1.5e-2), "Code_Aster on the same mesh; S4 is 1.3 % stiffer here"


@pytest.mark.parametrize("model", ["shell", "beam"])
def test_a_code_aster_run_of_one_element_family_ends_ok_without_alarms(model, require_solver, tmp_path):
    """The cantilevered plate (DKT) and the cantilevered beam (POU_D_E), each step with two field outputs (the concept
    step's and one added): ``DIAGNOSTIC JOB : OK`` and no alarm in the .mess file.

    Measured, 18.1.8, before: both ended ``<A>_ALARM`` -- the shells <CALCULEL2_89> for SIPO_ELNO and SIPM_ELNO (beam
    fields), the beams <CALCULEL2_89> for SIGM_ELNO and <ELEMENTS4_4> for SIPM_ELNO (not on a GENERALE section), and
    both <CALCCHAMP_1> for every field asked for twice (one CALC_CHAMP per field output); 8 and 18 alarms.
    """
    import re

    require_solver("code_aster")
    if model == "shell":
        a, p, pl, _ = _plate("cant")
        p.concept_fem.constraints.add_curve_constraint(
            ConstraintConceptCurve("root", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dy", "dz", "rx", "ry", "rz")))
        )
        q = (0, 0, -500.0)
        p.concept_fem.loads.add_load_case(
            LoadConceptCase("LC_eu", [LoadConceptLine("EU", (L, 0, 0), (L, WID, 0), q, q)])
        )
        p.fem = p.to_fem_obj(0.125, use_quads=True)
        p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    else:
        a, p, bm = _cantilever()
    assert len(p.fem.steps[0].field_outputs) == 2
    _solve(a, "fam", "code_aster", tmp_path)
    mess = (tmp_path / "fam" / "fam.mess").read_text(encoding="utf-8", errors="replace")
    assert re.findall(r"DIAGNOSTIC JOB\s*:\s*(\S+)", mess) == ["OK"]
    assert re.findall(r"<A> <(\w+)>", mess) == []


# --- prescribed displacements -----------------------------------------------------------------------------------

#: The propped cantilever's cases and the tip's value in each: a mid-span load, a settlement, both, twice the second.
SETTLE_CASES = {"LC_pt": 0.0, "LC_set": -0.01, "LC_both": 0.02, "LCC": -0.02}


def _propped_cantilever():
    """Root clamped, the tip's dz prescribed (GeniE's ``prescribed`` support): 1 kN down at mid-span in LC_pt and
    LC_both, the tip settling -0.01 m in LC_set and +0.02 m in LC_both, and LCC = 2 x LC_set."""
    a, p, bm = _ipe300_beam()
    c = p.concept_fem.constraints
    c.add_point_constraint(ConstraintConceptPoint("root", (0, 0, 0), Dof.encastre()))
    tip_dofs = [Dof(d, "free") for d in ("dx", "dy", "rx", "ry", "rz")] + [Dof("dz", "prescribed")]
    sp = c.add_point_constraint(ConstraintConceptPoint("tip", (L, 0, 0), tip_dofs))
    ld = p.concept_fem.loads
    down = (0, 0, -1000.0)
    ld.add_load_case(LoadConceptCase("LC_pt", [LoadConceptPoint("P", (L / 2, 0, 0), down, (0, 0, 0))], 1))
    settle = [LoadConceptPrescribedDisplacement("PD", sp, (0, 0, SETTLE_CASES["LC_set"]))]
    lc_set = ld.add_load_case(LoadConceptCase("LC_set", settle, 2))
    both = [
        LoadConceptPrescribedDisplacement("PD2", sp, (0, 0, SETTLE_CASES["LC_both"])),
        LoadConceptPoint("P2", (L / 2, 0, 0), down, (0, 0, 0)),
    ]
    ld.add_load_case(LoadConceptCase("LC_both", both, 3))
    ld.add_load_case_combination(LoadConceptCaseCombination("LCC", [LoadConceptCaseFactored(lc_set, 2.0)]))
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, p, bm


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_prescribed_tip_settlement_reacts_3_ei_delta_over_l_cubed(fem_format, require_solver, tmp_path):
    """The propped cantilever, case by case: the tip lands on its value and reacts ``3 E I delta / L^3`` (plus
    ``5 P / 16`` = 312.5 N of the mid-span load), and mid-span deflects ``delta x^2 (3L - x) / (2 L^3)`` = 0.3125
    delta (plus ``-7 P L^3 / (768 E I)`` of the load).

    Measured, Code_Aster 18.1.8: R_tip -7874.0027753 N for -0.01 m (closed form -7874.0027753), 16060.505551 N in
    LC_both and -15748.005551 N in LCC, mid-span -3.125e-3 / 6.2152733803e-3 / -6.25e-3 m: the closed form to every
    printed digit. Before, the support's ``DZ=0`` and every case's settlement all held the tip in ``CHAR_MECA_GLOBAL``
    and Code_Aster stopped (<ASSEMBLA_26>, the dof "bloqué plusieurs fois"); the values were not written either.
    ``MACRO_ELAS_MULT`` cannot take a support value per case (<ASSEMBLA_45>), so this step is one ``MECA_STATIQUE``
    over an instant per case.

    CalculiX 2.23 does not solve a prescribed displacement on a U1 beam (mid-span -0.23375 m for -0.01 m; a prescribed
    rigid translation bent the beam), so the writer holds the dof at zero and names each settlement ``omitted``: the
    tip stays at 0 and each case is the plain propped cantilever.
    """
    require_solver(fem_format)
    from ada.fem.formats import conversion_report

    with conversion_report.collect() as report:
        a, p, bm = _propped_cantilever()
        solved = _solve(a, "pd", fem_format, tmp_path)
    ei = bm.material.model.E * bm.section.properties.Iy
    w_load, r_load = -7 * 1000.0 * L**3 / (768 * ei), 5 * 1000.0 / 16
    tip, mid = solved.node(L), solved.node(L / 2)
    for k, (case, delta) in enumerate(SETTLE_CASES.items(), start=1):
        loaded = case in ("LC_pt", "LC_both")
        if fem_format == "code_aster":
            assert solved.u(k, tip)[2] == pytest.approx(delta, abs=1e-15)
            assert solved.u(k, mid)[2] == pytest.approx(0.3125 * delta + (w_load if loaded else 0), rel=1e-9)
            r_tip = solved.reactions(k, [tip])[0][2]
            assert r_tip == pytest.approx(3 * ei * delta / L**3 + (r_load if loaded else 0), rel=1e-9)
            assert solved.reactions(k)[0][2] == pytest.approx(1000.0 if loaded else 0.0, abs=1e-6)
        else:
            assert solved.u(k, tip)[2] == 0.0
            assert solved.u(k, mid)[2] == pytest.approx(w_load if loaded else 0.0, rel=FRD, abs=1e-12)
    refused = [f for f in report.of_kind("omitted") if f.keyword == "*BOUNDARY"]
    if fem_format == "calculix":
        (finding,) = refused
        assert sorted([finding.subject, *finding.other_subjects]) == ["tip_LCC", "tip_LC_both", "tip_LC_set"]
        assert "U1 beam" in finding.reason
    else:
        assert not report.of_kind("omitted"), report.summary()


def _strip_cantilever(dof: int, values: dict):
    """The plate strip in cylindrical bending (long edges held in dy and rx), its root clamped and its tip edge's
    ``dof`` prescribed per load case: an FE ``Bc`` per case naming it, as the concept conversion makes them."""
    from ada.fem import Bc, FemSet, LoadCase
    from ada.fem.constraints import BC_LOAD_CASE

    a, p, pl, mat = _plate("pdstrip")
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dz", "ry", "rz"))))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    p.fem = p.to_fem_obj(0.125, use_quads=True)
    tip = p.fem.add_set(FemSet("tip_edge", [n for n in p.fem.nodes if abs(n.x - L) < 1e-9], FemSet.TYPES.NSET))
    p.fem.add_bc(Bc("tip_hold", tip, [dof]))
    step = p.fem.add_step(StepImplicitStatic("settle"))
    for name, v in values.items():
        p.fem.add_bc(Bc(f"tip_{name}", tip, [dof], magnitudes=[v], metadata={BC_LOAD_CASE: name}))
        step.add_loadcase(LoadCase(name, None, loads=[]))
    # A case with no settlement and no load: CalculiX solves it as a step of its own (Code_Aster leaves it out).
    step.add_loadcase(LoadCase("LC_none", None, loads=[]))
    step.add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, p, mat


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_prescribed_plate_edge_settlement_and_rotation(fem_format, require_solver, tmp_path):
    """The strip's tip edge settles (dz -0.01 m, then +0.02 m) or turns (ry 0.01, then -0.02 rad), case by case: the
    root reacts ``3 D b delta / L^3`` and mid-span deflects 0.3125 delta; a turned tip makes the root react
    ``D b theta / L`` and mid-span deflect ``-theta L / 8``.

    Measured, quads at 0.125 m: Code_Aster (DKT) R = 4.5072115406 N against 4.5072115385 (+4.7e-10), mid-span
    -3.1250000002e-3 m; M = -24.038461557 N m against 24.038461538 (+7.7e-10). CalculiX (S4, a C3D8I layer) R =
    4.508288 N (+2.39e-4; 4.50747 at 0.0625 m, +5.7e-5) and mid-span -3.12455e-3 m (-1.44e-4); M = -24.0384 N m
    (-2.6e-6). Before, neither writer wrote the values: CalculiX held the edge at zero, Code_Aster stopped (a dof held
    twice).
    """
    require_solver(fem_format)
    for dof, values in ((3, {"LC1": -0.01, "LC2": 0.02}), (5, {"LC1": 0.01, "LC2": -0.02})):
        a, p, mat = _strip_cantilever(dof, values)
        solved = _solve(a, f"pd{dof}", fem_format, tmp_path)
        d = mat.model.E * T**3 / (12 * (1 - mat.model.v**2))
        root = [n for n, c in solved.coords.items() if abs(c[0]) < 1e-9]
        mid = solved.node(L / 2)
        for k, v in enumerate(values.values(), start=1):
            force, moment = solved.reactions(k, root)
            w = solved.u(k, mid)[2]
            if dof == 3:
                closed_r, closed_w = -3 * d * WID * v / L**3, 0.3125 * v
                if fem_format == "code_aster":
                    assert force[2] == pytest.approx(closed_r, rel=1e-9)
                    assert w == pytest.approx(closed_w, rel=1e-9)
                else:
                    assert force[2] / closed_r - 1 == pytest.approx(2.38e-4, abs=1e-5)
                    assert w / closed_w - 1 == pytest.approx(-1.44e-4, abs=1e-5)
            else:
                closed_m, closed_w = -d * WID * v / L, -v * L / 8
                assert moment[1] == pytest.approx(closed_m, rel=1e-9 if fem_format == "code_aster" else FRD)
                assert w == pytest.approx(closed_w, rel=1e-9 if fem_format == "code_aster" else FRD)
        if fem_format == "calculix":
            # ccx carries a *BOUNDARY value into the next step: the case after LC2 names no settlement and must put
            # the edge back at zero, not keep LC2's 0.02.
            assert solved.u(3, solved.node(L))[2] == 0.0
            assert solved.u(3, mid)[2] == 0.0


def _combined_settlements(model: str):
    """LCC = LC1 + LC2, both cases prescribing the same support, as the concept conversion makes them.

    * ``beam``: the IPE300 cantilever clamped at the root, its tip a support prescribing dx, dz and rz; LC1 moves dx by
      -0.003 and loads mid-span with 10 kN down, LC2 moves dx, dz, rz by 0.005, -0.01, 0.001 (GeniE's frames-fixture
      values, as ``test_concept_supports_sestra`` solves them in Sestra).
    * ``plate``: the strip cantilevered at x = 0, its tip corner (L, 0) a support prescribing dz and ry; LC1 moves dz
      by -0.01 under 1000 Pa, LC2 moves dz by 0.02 and ry by 0.01.
    """
    c_tip = {"beam": ("dx", "dz", "rz"), "plate": ("dz", "ry")}[model]
    if model == "beam":
        a, p, bm = _ipe300_beam("lcc")
        p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("root", (0, 0, 0), Dof.encastre()))
        lc1_loads = [LoadConceptPoint("P", (L / 2, 0, 0), (0, 0, -10000.0), (0, 0, 0))]
        pd1, pd2 = ((-0.003, 0, 0), (0, 0, 0)), ((0.005, 0, -0.01), (0, 0, 0.001))
    else:
        a, p, pl, _ = _plate("lccp")
        p.concept_fem.constraints.add_curve_constraint(
            ConstraintConceptCurve("root", (0, 0, 0), (0, WID, 0), Dof.encastre())
        )
        lc1_loads = [LoadConceptSurface("P", pl, pressure=1000.0, side="front")]
        pd1, pd2 = ((0, 0, -0.01), (0, 0, 0)), ((0, 0, 0.02), (0, 0.01, 0))
    tip_dofs = [Dof(d, "prescribed" if d in c_tip else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
    sp = p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("tip", (L, 0, 0), tip_dofs))
    ld = p.concept_fem.loads
    lc1 = ld.add_load_case(LoadConceptCase("LC1", [LoadConceptPrescribedDisplacement("PD1", sp, *pd1), *lc1_loads]))
    lc2 = ld.add_load_case(LoadConceptCase("LC2", [LoadConceptPrescribedDisplacement("PD2", sp, *pd2)]))
    terms = [LoadConceptCaseFactored(lc1, 1.0), LoadConceptCaseFactored(lc2, 1.0)]
    ld.add_load_case_combination(LoadConceptCaseCombination("LCC", terms))
    if model == "beam":
        p.fem = p.to_fem_obj(0.5, bm_repr="line")
    else:
        p.fem = p.to_fem_obj(0.125, use_quads=True)
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, p


@pytest.mark.parametrize("fem_format, model", [("code_aster", "beam"), ("code_aster", "plate"), ("calculix", "plate")])
def test_a_combination_of_two_settlement_cases_solves_as_their_sum(fem_format, model, require_solver, tmp_path):
    """LCC's every nodal displacement is LC1's plus LC2's, and the tip lands on the summed settlement.

    The concept conversion made one scaled ``Bc`` per term, both on the tip's set and both naming LCC; these writers
    (``ada.fem.formats.prescribed.case_values``) kept the first and reported the second ``omitted``, so LCC solved
    with LC1's tip values. Since the conversion sums the terms into one ``Bc`` per support (#435), LCC is the sum.

    Measured, Code_Aster 18.1.8: beam tip (dx, dz, rz) = (0.002, -0.01, 0.001), max |u_LCC - (u_LC1 + u_LC2)| =
    5.2e-18 (|u| up to 0.01); plate tip (dz, ry) = (0.01, 0.01), 4.8e-16 (|u| up to 0.075). CalculiX 2.23 (S4) on the
    plate: tip dz 0.01, 1.0e-7 (|u| up to 0.0626), inside what six printed digits allow per value. Before (one Bc per
    term, the conversion's ``combination`` of the old #435 head), LCC's tip took LC1's values and "tip_LC2_LCC" was
    reported omitted: Code_Aster beam tip (dx, dz, rz) = (-0.003, 0, 0), max |u_LCC - (u_LC1 + u_LC2)| = 0.01; plate
    tip (dz, ry) = (-0.01, 0), 0.0207; CalculiX plate tip dz -0.01, 0.0225.
    """
    require_solver(fem_format)
    from ada.fem.formats import conversion_report

    with conversion_report.collect() as report:
        a, p = _combined_settlements(model)
        solved = _solve(a, f"lcc{model[0]}", fem_format, tmp_path)
    u1, u2, uc = (solved._fields("u", k) for k in (1, 2, 3))
    tip = uc[solved.node(L)]
    for n in uc:
        # Code_Aster's doubles: measured 4.8e-16 at most; CalculiX prints six digits (FRD) of each of the three values
        bound = 1e-13 if fem_format == "code_aster" else FRD * (np.abs(uc[n]) + np.abs(u1[n]) + np.abs(u2[n]))
        assert np.all(np.abs(uc[n] - u1[n] - u2[n]) <= bound), n
    if model == "beam":
        assert tip[[0, 2, 5]] == pytest.approx([0.002, -0.01, 0.001], abs=1e-15)
    elif fem_format == "code_aster":
        assert tip[[2, 4]] == pytest.approx([0.01, 0.01], abs=1e-15)
    else:
        assert tip[2] == 0.01  # CalculiX's .frd prints U only
    assert not report.of_kind("omitted"), report.summary()


# --- edge line loads on first- and second-order shells -----------------------------------------------------------

#: The edge loads, down along the strip's y = 0 edge from x = 1 to x = 3 m: (q at x = 1, q at x = 3) in N/m.
EDGE_CASES = {"LC_eu": (1000.0, 1000.0), "LC_elin": (1000.0, 3000.0)}
EDGE_FROM, EDGE_TO = 1.0, 3.0
#: CalculiX's support totals against statics, relative, as measured (see the test).
EDGE_TOTALS = {(1, True): 6e-7, (2, True): 8e-7, (2, False): 1.2e-6}


@pytest.mark.parametrize("fem_format", SOLVERS)
@pytest.mark.parametrize("order, quads", [(1, True), (2, True), (2, False)], ids=["quad4", "quad8", "tri6"])
def test_an_edge_load_reacts_as_its_resultant_on_first_and_second_order_shells(
    fem_format, order, quads, require_solver, tmp_path
):
    """The strip simply supported on its short edges (x = 0: dx, dy, dz; x = 4: dy, dz), a uniform and a linear load
    down its y = 0 edge over 1..3 m: the reactions sum to ``F = (q1 + q2) l / 2`` and their moment about y to
    ``-F x_c``, ``x_c = 1 + l (q1 + 2 q2) / (3 (q1 + q2))`` -- 2000 N at 2 m and 4000 N at 2.1667 m.

    On 8-node quads and 6-node triangles the load became nothing (reported ``omitted``: no shell element edge along
    it), so there was no reaction. Measured: Code_Aster to 1e-9 on all three meshes (DKT on quads; COQUE_3D on the 9-
    and 7-node cells it makes of 8- and 6-node shells: 1999.999999 N, -4000.0 N m); CalculiX S4 2000 / 4000.022 N, S8
    1999.8828 / 4000.1266 N, S6 1999.9906 / 4000.109 N, each within what six printed digits per nodal force allow
    (``5e-6 sum |RF|``: 0.056, 1.08, 0.49 N for the uniform case), which is the tolerance on those sums.

    CalculiX also prints each support's total (``*NODE PRINT, TOTALS=ONLY``, summed before printing), and those meet
    statics far closer -- measured, x = 0 and x = L: S4 999.9995 / 999.9995 N and 1833.332 / 2166.666 N (-5e-7,
    -3.1e-7), S8 999.9992 / 999.9994 and 1833.332 / 2166.665 (-7e-7, -7.7e-7), S6 999.9994 / 999.9989 and 1833.332 /
    2166.665 (-8.5e-7, -1.1e-6) against 1000 / 1000 and 1833.333 / 2166.667 N: :data:`EDGE_TOTALS`.
    """
    from ada.fem.meshing import GmshOptions

    require_solver(fem_format)
    a, p, pl, _ = _plate("edge")
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dy", "dz"))))
    c.add_curve_constraint(ConstraintConceptCurve("xL", (L, 0, 0), (L, WID, 0), _dofs(("dy", "dz"))))
    for name, (q1, q2) in EDGE_CASES.items():
        line = LoadConceptLine("E" + name, (EDGE_FROM, 0, 0), (EDGE_TO, 0, 0), (0, 0, -q1), (0, 0, -q2))
        p.concept_fem.loads.add_load_case(LoadConceptCase(name, [line]))
    p.fem = p.to_fem_obj(0.125, use_quads=quads, options=GmshOptions(Mesh_ElementOrder=order))
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, f"edge{order}{int(quads)}", fem_format, tmp_path)

    supports = [n for n, xyz in solved.coords.items() if abs(xyz[0]) < 1e-9 or abs(xyz[0] - L) < 1e-9]
    ell = EDGE_TO - EDGE_FROM
    for k, (q1, q2) in enumerate(EDGE_CASES.values(), start=1):
        total = (q1 + q2) * ell / 2
        x_c = EDGE_FROM + ell * (q1 + 2 * q2) / (3 * (q1 + q2))
        force, moment = solved.reactions(k, supports)
        if fem_format == "code_aster":
            tol_f, tol_m = 1e-9 * total, 1e-9 * total * x_c
        else:
            rf = solved._fields("rf", k)
            tol_f = 5e-6 * sum(abs(rf[n][2]) for n in supports)
            tol_m = 5e-6 * sum(
                abs(solved.coords[n][0] * rf[n][2]) + abs(solved.coords[n][2] * rf[n][0]) for n in supports
            )
        assert abs(force[2] - total) <= tol_f
        assert abs(moment[1] + total * x_c) <= tol_m
        assert abs(moment[0]) <= tol_m, "the load acts along y = 0"
        if fem_format == "calculix":
            # the supports' totals (*NODE PRINT, TOTALS=ONLY): x = 0 carries F (1 - x_c / L), x = L carries F x_c / L
            from ada.fem.formats.calculix.results.read_dat import read_reaction_totals

            totals = read_reaction_totals(solved.dat)
            r0, r_l = totals[(k, "X0_SET")][2], totals[(k, "XL_SET")][2]
            tol = EDGE_TOTALS[(order, quads)]
            assert r0 + r_l == pytest.approx(total, rel=tol)
            assert r_l == pytest.approx(total * x_c / L, rel=tol)


# --- torsion ------------------------------------------------------------------------------------------------------


def _independent_torsion_constant(section: str) -> float:
    """The St Venant torsion constant from a closed form of its own, not adapy's section properties: Bredt's
    ``4 A_m^2 / sum(s / t)`` for the thin-walled box (wall mid-lines 0.29 x 0.19 m, all walls 10 mm), the exact
    ``pi (D^4 - d^4) / 32`` for the tube, and for the IPE300 its catalogue value, ``It = 20.12 cm^4`` (ArcelorMittal;
    the open thin-walled ``sum(b t^3) / 3`` without the root fillets gives 15.6 cm^4)."""
    if section == "BG300x200x10x10":
        a_m, s_over_t = 0.29 * 0.19, 2 * (0.29 + 0.19) / 0.01
        return 4 * a_m**2 / s_over_t
    if section == "OD300x10":
        return np.pi * (0.3**4 - 0.28**4) / 32
    return 20.12e-8


@pytest.mark.parametrize("fem_format", SOLVERS)
@pytest.mark.parametrize("section", ["IPE300", "BG300x200x10x10", "OD300x10"])
def test_a_tip_torque_twists_by_t_l_over_g_j(fem_format, section, require_solver, tmp_path):
    """A 4 m cantilever in 0.5 m two-node beams under a tip torque of 1 kN m: Code_Aster's POU_D_E twists by
    ``T L / (G J)`` with ``J`` the section's torsion constant as written (``JX``), to 1e-9 -- IPE300 0.24460430930
    rad, box 3.9149127591e-4, tube 2.5823438730e-4.

    The reference for ``J``: adapy's ``Ix`` is what the writers carry, and it is checked here against a closed form of
    its own (:func:`_independent_torsion_constant`): Bredt's exactly for this box (all walls equal -- with unequal
    walls ``calc_box`` divides the top flange's width by the web's thickness), the tube's exactly, and the IPE300's
    catalogue 20.12 cm^4 to 0.7 % (adapy 20.25 cm^4, its fillets modelled otherwise).

    CalculiX's U1 beam cannot carry ``J``: it twists by ``T L / (G (Iy + Iz))`` (manual 6.2.46; measured 5.75745e-4
    rad for the IPE300, 425 times too stiff; 2.68218e-4 for the box, 1.46 times), which is right only for the tube.
    The writer names the torsion constant ``omitted`` for the IPE and the box, and says nothing for the tube.
    """
    from ada.fem.concept.loads import LoadConceptCase as Case
    from ada.fem.formats import conversion_report

    require_solver(fem_format)
    torque = 1000.0
    with conversion_report.collect() as report:
        bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), section, ada.Material("S355", CarbonSteel("S355")))
        p = ada.Part("beam") / bm
        a = ada.Assembly("tor") / p
        p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("fix", (0, 0, 0), Dof.encastre()))
        p.concept_fem.loads.add_load_case(Case("LC_T", [LoadConceptPoint("T", (L, 0, 0), (0, 0, 0), (torque, 0, 0))]))
        p.fem = p.to_fem_obj(0.5, bm_repr="line")
        solved = _solve(a, "tor", fem_format, tmp_path)
    props, mat = bm.section.properties, bm.material.model
    g = mat.E / (2 * (1 + mat.v))
    j_closed = _independent_torsion_constant(section)
    assert props.Ix == pytest.approx(j_closed, rel=1e-12 if section != "IPE300" else 7e-3)
    twist = solved.u(1, solved.node(L))[3]
    omitted = [f for f in report.of_kind("omitted") if f.keyword == "*BEAM SECTION"]
    if fem_format == "code_aster":
        assert twist == pytest.approx(torque * L / (g * props.Ix), rel=1e-9)
        assert not omitted
    else:
        assert twist == pytest.approx(torque * L / (g * (props.Iy + props.Iz)), rel=FRD)
        if section == "OD300x10":
            assert twist == pytest.approx(torque * L / (g * j_closed), rel=FRD)
            assert not omitted
        else:
            (finding,) = omitted
            assert finding.details["torsion_constant"] == props.Ix
            assert "torsion constant" in finding.reason


# --- a failed solve is named, not read ----------------------------------------------------------------------------


def _raised(call):
    """The exception ``call`` raises, whatever its type (so that a run before the runners named failures fails on the
    assertion, with the type it raised then, and not on an import)."""
    with pytest.raises(Exception) as info:
        call()
    return info.value


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_mechanism_stops_the_solve_by_name(fem_format, require_solver, tmp_path):
    """The beam held in dz only at both ends: nothing holds it along x or about its axis. Each solver stops, and the
    runner raises ``FEASolveFailed`` with the solver's own name for it, instead of reading results.

    Measured: Code_Aster stops at <FACTOR_11> ("la matrice est singulière", ``DIAGNOSTIC JOB : <S>_ERROR``) and
    wrote no .rmed -- the run raised ``FileNotFoundError: FEM result file does not exist``; CalculiX's output stops
    after "Factoring the system of equations", ``spooles.out`` says "matrix found to be singular", and the reader of
    the .frd it left raised ``ReadFrdFailedException: No element information from Calculix``.
    """
    require_solver(fem_format)
    a, p, bm = _ipe300_beam()
    c = p.concept_fem.constraints
    c.add_point_constraint(ConstraintConceptPoint("a", (0, 0, 0), _dofs(("dz",))))
    c.add_point_constraint(ConstraintConceptPoint("b", (L, 0, 0), _dofs(("dz",))))
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC", [LoadConceptPoint("P", (L / 2, 0, 0), (0, 0, -1000.0), (0, 0, 0))])
    )
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    err = _raised(lambda: a.to_fem("mech", fem_format, scratch_dir=tmp_path, overwrite=True, execute=True))
    assert type(err).__name__ == "FEASolveFailed", repr(err)
    code, file = {"code_aster": ("FACTOR_11", "mech.mess"), "calculix": ("spooles_singular", "run_log.txt")}[fem_format]
    assert (err.solver, err.code, err.file.name) == (fem_format, code, file)
    assert err.file.exists() and "mechanism" in err.hint


def _clamped_cantilever(force):
    from ada.fem import Bc, FemSet, Load

    a, p, bm = _ipe300_beam()
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    root = p.fem.add_set(FemSet("root", [n for n in p.fem.nodes if abs(n.x) < 1e-9], FemSet.TYPES.NSET))
    tip = p.fem.add_set(FemSet("tipn", [n for n in p.fem.nodes if abs(n.x - L) < 1e-9], FemSet.TYPES.NSET))
    p.fem.add_bc(Bc("fix", root, [1, 2, 3, 4, 5, 6]))
    step = a.fem.add_step(StepImplicitStatic("s"))
    step.add_load(Load("F", Load.TYPES.FORCE, force, dof=3, fem_set=tip))
    step.add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, bm


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_an_existing_result_is_reused_only_when_its_run_finished(fem_format, require_solver, tmp_path):
    """``to_fem(..., overwrite=False)`` returned an existing result file without looking at the run that wrote it,
    and a failed run can leave one (Code_Aster's MED file holds what was printed before the command that stopped;
    CalculiX's .frd its header). Here the clamped cantilever is solved under 1 kN at the tip, then the run's verdict
    is made a failure by hand -- Code_Aster's ``DIAGNOSTIC JOB`` set to ``<S>_ERROR``, CalculiX's saved output
    without ``Job finished`` -- and the model asked for again under 2 kN: not to be run, it is refused by name; to be
    run, it is written and run again (tip ``-2 P L^3 / (3 EI)``), and that result, whose run finished, is reused.
    A result whose run left no verdict (no .mess, no saved output) is refused as well.

    Measured before the change: the second call returned the 1 kN result, tip -1.27e-3 m, without a word.
    """
    require_solver(fem_format)
    import re

    a1, bm = _clamped_cantilever(-1000.0)
    ei = bm.material.model.E * bm.section.properties.Iy
    tol = FRD if fem_format == "calculix" else 1e-9

    def tip_w(res):
        solved = Solved(fem_format, res)
        return solved.u(1, solved.node(L))[2] if fem_format == "calculix" else solved.u(None, solved.node(L))[2]

    first = a1.to_fem("reuse", fem_format, scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    assert tip_w(first) == pytest.approx(-1000.0 * L**3 / (3 * ei), rel=tol)

    verdict = tmp_path / "reuse" / ("reuse.mess" if fem_format == "code_aster" else "run_log.txt")
    text = verdict.read_text(encoding="utf-8", errors="replace")
    if fem_format == "code_aster":
        failed = re.sub(r"DIAGNOSTIC JOB\s*:\s*\S+", "DIAGNOSTIC JOB : <S>_ERROR", text)
    else:
        failed = text.replace("Job finished", "")
    assert failed != text
    verdict.write_text(failed, encoding="utf-8")

    a2, _ = _clamped_cantilever(-2000.0)
    err = _raised(lambda: a2.to_fem("reuse", fem_format, scratch_dir=tmp_path, execute=False))
    assert type(err).__name__ == "FEASolveFailed", repr(err)
    assert (err.solver, err.file) == (fem_format, verdict)
    second = a2.to_fem("reuse", fem_format, scratch_dir=tmp_path, execute=True, exit_on_complete=False)
    assert tip_w(second) == pytest.approx(-2000.0 * L**3 / (3 * ei), rel=tol)
    third = a1.to_fem("reuse", fem_format, scratch_dir=tmp_path, execute=False)
    assert tip_w(third) == pytest.approx(-2000.0 * L**3 / (3 * ei), rel=tol), "the finished run's result, reused"

    verdict.unlink()
    err = _raised(lambda: a1.to_fem("reuse", fem_format, scratch_dir=tmp_path, execute=False))
    assert type(err).__name__ == "FEASolveFailed", repr(err)
    assert err.code == {"code_aster": "NO_MESS", "calculix": "NO_RUN_LOG"}[fem_format]


def test_a_calculix_input_error_stops_the_solve_by_name(require_solver, tmp_path):
    """A deck whose load names a node that does not exist (written by hand into adapy's deck): ccx 2.23 prints
    ``*ERROR reading *CLOAD: node 99999 is not defined`` and ``*ERROR in calinput: ... CalculiX stops`` and leaves a
    .frd without results. The runner raises by the first error's keyword, with every one listed and the output saved
    to ``run_log.txt``."""
    from ada.fem.formats.calculix.execute import run_calculix

    require_solver("calculix")
    a, p, bm = _cantilever()
    a.to_fem("bad", "calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    inp = tmp_path / "bad" / "bad.inp"
    deck = inp.read_text()
    assert deck.count("*Cload\nLC_F") == 1
    inp.write_text(deck.replace("*Cload\nLC_F", "*Cload\n99999, 3, 1.0\nLC_F"))
    err = _raised(lambda: run_calculix(inp, cpus=1))
    assert type(err).__name__ == "FEASolveFailed", repr(err)
    assert (err.code, err.codes) == ("reading_CLOAD", ("reading_CLOAD", "calinput"))
    assert "99999 is not defined" in err.message
    assert err.file == tmp_path / "bad" / "run_log.txt" and "99999" in err.file.read_text()


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_overlapping_supports_hold_a_shared_node_once(fem_format, require_solver, tmp_path):
    """The strip under 1000 Pa with its x = 0 edge held in dx, dy and dz and its long edges in dy and rx: the corners
    (0, 0) and (0, 0.5) are held in dy by two supports. Each end reacts ``q A / 2`` = 1000 N, mid-span deflects as the
    strip whose supports do not overlap.

    Measured: Code_Aster stopped at <ASSEMBLA_26> ("le noeud: 2 composante: DY est bloqué plusieurs fois") -- each
    support was a charge of its own; with every support a row of one charge it gives -0.173197916686256 m (Sestra's
    -0.1731979101896 on this mesh; the same as without the overlap) and reactions 1000 N per end, 2000 N in all
    (1999.9999999999993). CalculiX takes the overlap as it is: -0.173065 m (S4, -0.155 % of the closed form).
    """
    require_solver(fem_format)
    a, p, pl, mat = _plate("ovl")
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dy", "dz"))))
    c.add_curve_constraint(ConstraintConceptCurve("x4", (L, 0, 0), (L, WID, 0), _dofs(("dz",))))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    loads = p.concept_fem.loads
    loads.add_load_case(LoadConceptCase("LC_p", [LoadConceptSurface("P", pl, pressure=1000.0, side="front")]))
    p.fem = p.to_fem_obj(0.125, use_quads=True)
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, "ovl", fem_format, tmp_path)

    d = mat.model.E * T**3 / (12 * (1 - mat.model.v**2))
    w = solved.u(1, solved.node(L / 2))[2]
    if fem_format == "code_aster":
        assert w == pytest.approx(-0.1731979101896286, rel=1e-6), "Sestra's consistent-load answer on this mesh"
        for x in (0.0, L):
            end = [n for n, xyz in solved.coords.items() if abs(xyz[0] - x) < 1e-9]
            assert solved.reactions(1, end)[0][2] == pytest.approx(1000.0 * L * WID / 2, rel=1e-9)
        assert solved.reactions(1)[0][2] == pytest.approx(1000.0 * L * WID, rel=1e-9)
    else:
        assert w / (-5 * 1000.0 * L**4 / (384 * d)) - 1 == pytest.approx(-1.55e-3, abs=2e-5)


# --- thin second-order shells in Code_Aster (COQUE_3D) ------------------------------------------------------------


def _cylindrical_cantilever(name, t):
    """The strip as a cantilever in cylindrical bending, 8-node shells at 0.0625 m: root clamped, long edges held in dy
    and rx, 500 N/m down the tip edge."""
    from ada.fem.meshing import GmshOptions

    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate(name, [(0, 0), (L, 0), (L, WID), (0, WID)], t, mat=mat)
    p = ada.Part(name.capitalize()) / pl
    a = ada.Assembly(f"{name}_a") / p
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("root", (0, 0, 0), (0, WID, 0), Dof.encastre()))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    q = (0, 0, -500.0)
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC_e", [LoadConceptLine("E", (L, 0, 0), (L, WID, 0), q, q)]))
    p.fem = p.to_fem_obj(0.0625, use_quads=True, options=GmshOptions(Mesh_ElementOrder=2))
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, mat


def test_a_thin_coque_3d_cantilever_deflects_as_a_mindlin_plate(require_solver, tmp_path):
    """The 10 mm strip on 8-node shells (``COQUE_3D`` on 9-node cells): the tip deflects ``q L^3 / (3 D) + q L /
    (5/6 G t)`` = 0.554669638 m (Mindlin; the shear term 2.97e-6 m) and the root reacts 250 N and 1000 N m.

    Measured, Code_Aster 18.1.8: it stopped at <FACTOR_57>, MUMPS's error estimate 6.88e-6 over RESI_RELA = 1e-6. The
    system is ill-conditioned (thin Mindlin shell; see ``static_lin._solver_str``), the answer is not: with the check
    off, tip -0.5546696376 m (-8.3e-10 relative), root 249.99999740 N (-1.04e-8) and 999.9999969 N m (-3.1e-9);
    MULT_FRONT -0.5546696381 m (-3.3e-11). Written now with ``RESI_RELA=1e-4``; the tolerances are those measured.
    """
    require_solver("code_aster")
    a, mat = _cylindrical_cantilever("cyl", T)
    solved = _solve(a, "cyl", "code_aster", tmp_path)
    e, nu = mat.model.E, mat.model.v
    d, g = e * T**3 / (12 * (1 - nu**2)), e / (2 * (1 + nu))
    closed = 500.0 * L**3 / (3 * d) + 500.0 * L / (5 / 6 * g * T)
    tip = [n for n, xyz in solved.coords.items() if abs(xyz[0] - L) < 1e-9]
    for n in tip:
        assert -solved.u(1, n)[2] == pytest.approx(closed, rel=2e-9)
    root = [n for n, xyz in solved.coords.items() if abs(xyz[0]) < 1e-9]
    force, moment = solved.reactions(1, root)
    assert force[2] == pytest.approx(250.0, rel=2e-8)
    assert -moment[1] == pytest.approx(1000.0, rel=1e-8)


def test_a_thinner_coque_3d_cantilever_stops_by_name(require_solver, tmp_path):
    """The same strip 2 mm thick: MUMPS's estimate 9.85e-4 is over even 1e-4 (the answer's error with the check off:
    +1.05e-6 on the tip deflection, +1.7e-6 on the root reaction), and the runner raises <FACTOR_57> by name with
    what to do."""
    require_solver("code_aster")
    a, _ = _cylindrical_cantilever("thin", 0.002)
    err = _raised(lambda: a.to_fem("thin", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True))
    assert type(err).__name__ == "FEASolveFailed", repr(err)
    assert err.code == "FACTOR_57" and "RESI_RELA" in err.message and "COQUE_3D" in err.hint


# --- two geometrically nonlinear steps in Code_Aster ----------------------------------------------------------------


def test_two_nlgeom_steps_are_both_solved_and_read(require_solver, tmp_path):
    """The 10 mm strip cantilevered in cylindrical bending (4-node shells, DKT, at 0.125 m), step 1 nlgeom 500 N/m down
    the tip edge, step 2 nlgeom 1000 N/m more (carrying step 1's load): the tips deflect ``q L^3 / (3 D)`` for 500 and
    1500 N/m.

    Measured, Code_Aster 18.1.8: the second step stopped at <MED2_4> ("Le champ 'DISP' est déjà présent dans le
    fichier MED") -- both printed their fields as ``DISP``; before the runner named failures, step 1's fields read back
    as the whole answer. Now the second step's fields are ``result2_DISP`` etc.

    The answers are the *linear* closed form -- measured 0.5546666667195 and 1.664000000159 m, +9.53e-11 -- although
    the tip deflects 0.55 and 1.66 m on 4 m: the step is ``STAT_NON_LINE`` with ``DEFORMATION='PETIT'`` (small displacements)
    and the default elastic ``RELATION``. That is reported ``approximated`` for each step.
    """
    from ada.fem.formats import conversion_report

    require_solver("code_aster")
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("nl", [(0, 0), (L, 0), (L, WID), (0, WID)], T, mat=mat)
    p = ada.Part("Nl") / pl
    a = ada.Assembly("nl_a") / p
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("root", (0, 0, 0), (0, WID, 0), Dof.encastre()))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    for name, q in (("LC_a", 500.0), ("LC_b", 1000.0)):
        line = LoadConceptLine("E" + name, (L, 0, 0), (L, WID, 0), (0, 0, -q), (0, 0, -q))
        p.concept_fem.loads.add_load_case(LoadConceptCase(name, [line]))
    p.fem = p.to_fem_obj(0.125, use_quads=True)
    (concept_step,) = p.fem.steps
    loads = {name: lc.loads[0] for name, lc in concept_step.load_cases.items()}
    p.fem.steps.remove(concept_step)
    for k, name in enumerate(("LC_a", "LC_b"), start=1):
        step = a.fem.add_step(StepImplicitStatic(f"nl{k}", nl_geom=True))
        step.add_load(loads[name])
    with conversion_report.collect() as report:
        a.to_fem("nl2", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.utils import default_fem_res_path

    res = ada.from_fem_res(default_fem_res_path("nl2", scratch_dir=tmp_path, fem_format=FEATypes.CODE_ASTER))
    coords = {int(i): np.asarray(xyz) for i, xyz in zip(res.mesh.nodes.identifiers, res.mesh.nodes.coords)}
    tip = [n for n, xyz in coords.items() if abs(xyz[0] - L) < 1e-9]
    d = mat.model.E * T**3 / (12 * (1 - mat.model.v**2))
    for field, q in (("DISP", 500.0), ("result2_DISP", 1500.0)):
        (last,) = [f for f in res.results if f.name.split("[")[0] == field and abs(f.step - 1.0) < 1e-9]
        u = {int(row[0]): row[1:] for row in np.asarray(last.values)}
        for n in tip:
            assert -u[n][2] == pytest.approx(q * L**3 / (3 * d), rel=2e-10)
    (finding,) = [f for f in report.of_kind("approximated") if f.keyword == "STAT_NON_LINE"]
    assert sorted([finding.subject, *finding.other_subjects]) == ["nl1", "nl2"]
    assert "DEFORMATION='PETIT'" in finding.reason


@pytest.mark.parametrize("quads", [True, False], ids=["quad8", "tri6"])
def test_second_order_shell_stresses_are_printed_at_the_faces(quads, require_solver, tmp_path):
    """The strip in cylindrical bending under 1000 Pa on 8-node quads / 6-node triangles at 0.125 m (``COQUE_3D``):
    at mid-span the faces carry ``6 M / t^2`` = 1.2e8 Pa along the span (``M = q L^2 / 8``) and ``nu`` times that
    across it (the plate held flat across), opposite on the two faces.

    Before, no stress of a second-order shell was printed (their layered fields stop IMPR_RESU at <MED2_20>; reported
    ``omitted``). Now each face's stress is extracted (``POST_CHAMP``/``EXTR_COQUE``, ``SIGM_NOEU``) and printed as
    ``result__SIGM_SUP_NOEU`` / ``..._INF_...``. Measured, principal stresses at the mid-span nodes: quads +6.51e-4
    (both; the element's own moment MYY = 2001.302 N m/m, 6 M / t^2 to 1e-11; +1.63e-4 at 0.0625 m), triangles
    3e-5..3.7e-4 along and 8e-5..1.5e-3 across (local axes vary per triangle, so principal values are compared).
    """
    from ada.fem.meshing import GmshOptions

    require_solver("code_aster")
    a, p, pl, mat = _plate("sig")
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dz"))))
    c.add_curve_constraint(ConstraintConceptCurve("x4", (L, 0, 0), (L, WID, 0), _dofs(("dz",))))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    loads = p.concept_fem.loads
    loads.add_load_case(LoadConceptCase("LC_p", [LoadConceptSurface("P", pl, pressure=1000.0, side="front")]))
    p.fem = p.to_fem_obj(0.125, use_quads=quads, options=GmshOptions(Mesh_ElementOrder=2))
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    solved = _solve(a, "sig", "code_aster", tmp_path)

    sigma = 6 * (1000.0 * L**2 / 8) / T**2
    nu = mat.model.v
    faces = {}
    for f in solved.res.results:
        for face in ("SUP", "INF"):
            if f.name.split("[")[0] == f"result__SIGM_{face}_NOEU":
                faces[face] = {int(row[0]): np.asarray(row[1:], dtype=float) for row in np.asarray(f.values)}
    assert sorted(faces) == ["INF", "SUP"]
    mid = [n for n, xyz in solved.coords.items() if abs(xyz[0] - L / 2) < 1e-9]
    tol_along, tol_across = (7e-4, 7e-4) if quads else (4e-4, 1.6e-3)
    for n in mid:
        sx, sy, _, sxy = faces["SUP"][n][:4]
        centre, radius = (sx + sy) / 2, np.hypot((sx - sy) / 2, sxy)
        assert abs(centre + radius) == pytest.approx(sigma, rel=tol_along)
        assert abs(centre - radius) == pytest.approx(nu * sigma, rel=tol_across)
        assert faces["INF"][n][:4] == pytest.approx(-faces["SUP"][n][:4], rel=1e-9, abs=1e-6 * sigma)


# --- loads named like the deck's own concepts -----------------------------------------------------------------


def _settled_cantilever_two_steps(load_name: str):
    """Root clamped, the tip's dz settled -0.01 m in every static step (a ``Bc`` with a value and no load case):
    step 1 a 1 kN point load down at mid-span named ``load_name``, step 2 another of that name at mid-span, with
    the first carried. The deck binds ``supports``, ``prescribed_zero``, ``result_pd`` and ``result2_pd`` itself."""
    from ada.fem import Bc, FemSet, LoadPoint

    a, p, bm = _ipe300_beam()
    p.fem = p.to_fem_obj(0.5, bm_repr="line")

    def nset(name, x):
        return p.fem.add_set(FemSet(name, [n for n in p.fem.nodes if abs(n.x - x) < 1e-9], "nset"))

    p.fem.add_bc(Bc("root", nset("root_set", 0.0), [1, 2, 3, 4, 5, 6]))
    p.fem.add_bc(Bc("tip", nset("tip_set", L), [3], magnitudes=[-0.01]))
    mid = nset("mid_set", L / 2)
    for k in (1, 2):
        step = a.fem.add_step(StepImplicitStatic(f"s{k}"))
        step.add_load(LoadPoint(load_name, -1000.0, mid, 3))
        step.add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    return a, bm


@pytest.mark.parametrize("load_name", ["supports", "prescribed_zero", "result_pd", "result2_pd"])
def test_loads_named_like_the_deck_s_own_charges_solve_as_their_closed_form(load_name, require_solver, tmp_path):
    """Two loads of one name in two general steps, the name one of the deck's own: each load is a concept of its own
    (``ld_<name>``, ``ld_<name>_2``), never the deck's charge of that name. The propped cantilever settling
    ``delta`` = -0.01 m: step 1 mid-span ``0.3125 delta - 7 P L^3 / (768 E I)``, tip reaction
    ``3 E I delta / L^3 + 5 P / 16``; step 2 carries step 1's load, so twice the load terms.

    Measured with Code_Aster 18.1.8 before the concept-name registry, each load bound under its own name: one load
    named ``supports`` or ``result_pd`` was rebound by the deck's charge of that name and the run stopped at
    <CHARGES9_10> (one charge given twice in EXCIT); two loads of one name were one concept and stopped it the same
    way, whatever the name."""
    require_solver("code_aster")
    a, bm = _settled_cantilever_two_steps(load_name)
    solved = _solve(a, "named", "code_aster", tmp_path)
    comm = (tmp_path / "named" / "named.comm").read_text(encoding="utf-8")
    assert f"ld_{load_name} = AFFE_CHAR_MECA(" in comm and f"ld_{load_name}_2 = AFFE_CHAR_MECA(" in comm
    ei = bm.material.model.E * bm.section.properties.Iy
    delta, w1, r1 = -0.01, -7 * 1000.0 * L**3 / (768 * ei), 5 * 1000.0 / 16
    tip, mid = solved.node(L), solved.node(L / 2)
    for k, result in enumerate(("result", "result2"), start=1):
        assert solved.u(None, tip, result)[2] == pytest.approx(delta, abs=1e-15)
        assert solved.u(None, mid, result)[2] == pytest.approx(0.3125 * delta + k * w1, rel=1e-9)
        r_tip = solved.reactions(None, [tip], result)[0][2]
        assert r_tip == pytest.approx(3 * ei * delta / L**3 + k * r1, rel=1e-9)
