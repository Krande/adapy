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

    def __init__(self, fem_format, res):
        self.fem_format = fem_format
        self.res = res
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
    return Solved(fem_format, ada.from_fem_res(res_path))


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
