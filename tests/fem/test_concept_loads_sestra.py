"""Sestra solves concept loads converted by ``Part.to_fem_obj``, against closed forms.

* A simply supported IPE300 beam, L = 4 m, meshed at 0.5 m (pin at x = 0, roller at x = 4), one load case per kind:
  a uniform line load, a partial one (1.3..2.9 m, BELOAD1 with L2 on the end element), a linear one (1000 -> 3000
  N/m), a point load at mid-span and one at 1.3 m (a node meshing made), gravity, and the combination 1.5 x uniform +
  gravity. Reactions are statics; the mid-span deflection is the beam's bending plus shear (EI, G Sharz -- Sestra's
  beam is exact under these loads), integrated exactly over the load.
* A plate strip 4.0 x 0.5 m, 10 mm, simply supported on its short edges and held in cylindrical bending, under a
  front pressure of 1000 Pa and under gravity: reactions q A, and the mid-span deflection against 5 q L^4 / (384 D).
* A cantilever plate of the same size under line loads along its tip edge and a long edge (BELLO2).

Measured with Sestra V11.3-00 (single precision SIN): every beam case to within 5e-8 of its closed form (uniform
2.449390595e-4 m against 2.449390565e-4; partial R_B 840.000000 against 840; linear R_B 4666.666504 against
4666.666667), the strip's pressure deflection -0.1731979102 m -- the consistent-nodal-load reference of
``test_sesam_pressure_load`` at this mesh, 0.078 % short of the closed form -0.1733333333 at 32 elements per span --
and its gravity deflection -0.1334358156 against the closed form -0.1334358177. Skips without Sestra.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
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
#: The strip's mid-span deflection under 1000 Pa at a 0.125 m mesh with the exact consistent nodal load
#: (``test_sesam_pressure_load.NODAL_LOAD_REFERENCE``).
STRIP_REFERENCE = -0.1731979101896286


def _sestra_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_sestra_exe() is None, reason="Sestra is not installed")


def _dofs(fixed, prescribed=()):
    kinds = {d: "fixed" for d in fixed} | {d: "prescribed" for d in prescribed}
    return [Dof(d, kinds.get(d, "free")) for d in ("dx", "dy", "dz", "rx", "ry", "rz")]


def _solve(a, p, name, tmp_path: pathlib.Path, size, **mesh_kw):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    p.fem = p.to_fem_obj(size, **mesh_kw)
    # No step is added: the concept load cases are the step (a GeniE model used to come with none, and no sestra.inp)
    a.to_fem(name, "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)
    lis = (tmp_path / name / "SESTRA.LIS").read_text(errors="replace")
    assert "Normal exit from Sestra" in lis, lis[-2000:]
    res = read_sin_file(tmp_path / name / f"{name}R1.SIN")
    assert res.sesam_case_names == {i: n for i, n in enumerate(p.fem.steps[0].load_cases, start=1)}
    return res, list(res.mesh.nodes.identifiers)


def _field(res, name, case):
    (values,) = [np.asarray(r.values) for r in res.results if r.name == name and r.step == case]
    return values


# --- the beam ----------------------------------------------------------------------------------------------------

BEAM_CASES = ["LC_u", "LC_part", "LC_lin", "LC_pmid", "LC_poff", "LC_grav", "LCC"]


@pytest.fixture(scope="module")
def beam(tmp_path_factory):
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    a = ada.Assembly("ss") / p
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
    res, ids = _solve(a, p, "ss", tmp_path_factory.mktemp("beam"), 0.5, bm_repr="line")
    return p, bm, res, ids


def _closed_forms(bm) -> dict[str, tuple[float, float, float]]:
    """``{case: (mid-span deflection downwards, reaction at x = L, total load)}``."""
    props, mat = bm.section.properties, bm.material.model
    ei, gas = mat.E * props.Iy, mat.E / (2 * (1 + mat.v)) * props.Sharz
    weight = mat.rho * props.Ax * G_ACC

    def influence(x, xi):
        if x <= xi:
            b = L - xi
            return b * x * (L**2 - b**2 - x**2) / (6 * ei * L) + b * x / (gas * L)
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
def test_the_beam_reacts_and_deflects_as_the_closed_form(beam, case):
    p, bm, res, ids = beam
    number = BEAM_CASES.index(case) + 1
    w, rb, total = _closed_forms(bm)[case]
    (mid,) = [n.id for n in p.fem.nodes if abs(n.x - L / 2) < 1e-9]
    (end,) = [n.id for n in p.fem.nodes if abs(n.x - L) < 1e-9]
    rf = _field(res, "REACTION-FORCE", number)
    assert rf[:, 3].sum() == pytest.approx(total, rel=1e-6)
    assert {int(r[0]): r[3] for r in rf}[end] == pytest.approx(rb, rel=1e-6)
    assert -_field(res, "sesam.nodes.displacement", number)[ids.index(mid)][4] == pytest.approx(w, rel=1e-6)


# --- the plates --------------------------------------------------------------------------------------------------


def _plate(name):
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate(name, [(0, 0), (L, 0), (L, WID), (0, WID)], T, mat=mat)
    p = ada.Part(name.capitalize()) / pl
    return ada.Assembly(f"{name}_a") / p, p, pl, mat


def test_a_strip_under_front_pressure_and_gravity_meets_the_closed_form(tmp_path):
    a, p, pl, mat = _plate("strip")
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dz"))))
    c.add_curve_constraint(ConstraintConceptCurve("x4", (L, 0, 0), (L, WID, 0), _dofs(("dz",))))
    c.add_curve_constraint(ConstraintConceptCurve("y0", (0, 0, 0), (L, 0, 0), _dofs(("dy", "rx"))))
    c.add_curve_constraint(ConstraintConceptCurve("y5", (0, WID, 0), (L, WID, 0), _dofs(("dy", "rx"))))
    loads = p.concept_fem.loads
    loads.add_load_case(LoadConceptCase("LC_p", [LoadConceptSurface("P", pl, pressure=1000.0, side="front")]))
    loads.add_load_case(LoadConceptCase("LC_g", [LoadConceptAccelerationField("G", (0, 0, -G_ACC))]))
    assert tuple(pl.poly.normal) == (0.0, 0.0, 1.0), "the front is +z: a front pressure pushes down"
    res, ids = _solve(a, p, "strip", tmp_path, 0.125, use_quads=True)

    (mid,) = [n.id for n in p.fem.nodes if abs(n.x - L / 2) < 1e-9 and abs(n.y) < 1e-9]
    d = mat.model.E * T**3 / (12 * (1 - mat.model.v**2))
    q_g = mat.model.rho * T * G_ACC
    w_p = _field(res, "sesam.nodes.displacement", 1)[ids.index(mid)][4]
    w_g = _field(res, "sesam.nodes.displacement", 2)[ids.index(mid)][4]
    assert _field(res, "REACTION-FORCE", 1)[:, 3].sum() == pytest.approx(1000.0 * L * WID, rel=1e-6)
    assert w_p == pytest.approx(STRIP_REFERENCE, rel=1e-6)
    assert w_p / (-5 * 1000.0 * L**4 / (384 * d)) == pytest.approx(0.99922, abs=1e-5), "0.078 % short at 0.125 m"
    assert _field(res, "REACTION-FORCE", 2)[:, 3].sum() == pytest.approx(q_g * L * WID, rel=1e-6)
    assert w_g == pytest.approx(-5 * q_g * L**4 / (384 * d), rel=1e-6)


def test_a_cantilever_plate_takes_its_edge_loads_on_the_edge_named(tmp_path):
    """Uniform along the tip edge: force and moment exact. Linear 500 -> 1500 N/m along it, and 100 -> 300 N/m along a
    long edge: the force exact and the moment short by the sum over the loaded element edges of h^2 dq / 60, which is
    the nodal moments of a Hermite-consistent load -- so Sestra applies BELLO2's consistent forces and drops their
    moments (measured: 146.09375 against 145.833 N m, and 1866.7188 against 1866.6667). Read with the edge's nodes the
    other way round the moment would be off by about 2 * sum h dq h / 6, i.e. 5 N m in the first case."""
    a, p, pl, _ = _plate("cant")
    p.concept_fem.constraints.add_curve_constraint(
        ConstraintConceptCurve("root", (0, 0, 0), (0, WID, 0), _dofs(("dx", "dy", "dz", "rx", "ry", "rz")))
    )
    ld = p.concept_fem.loads
    q = (0, 0, -500.0)
    ld.add_load_case(LoadConceptCase("LC_eu", [LoadConceptLine("EU", (L, 0, 0), (L, WID, 0), q, q)]))
    ld.add_load_case(LoadConceptCase("LC_el", [LoadConceptLine("EL", (L, 0, 0), (L, WID, 0), q, (0, 0, -1500.0))]))
    side = LoadConceptLine("ES", (0, WID, 0), (L, WID, 0), (0, 0, -100.0), (0, 0, -300.0))
    ld.add_load_case(LoadConceptCase("LC_side", [side]))
    res, _ = _solve(a, p, "cant", tmp_path, 0.125, use_quads=True)

    h = 0.125
    applied = {  # force, moment about the origin, and the Hermite nodal moments Sestra leaves out
        1: ((0, 0, -250.0), (-500 * WID**2 / 2, 250 * L, 0), (0, 0, 0)),
        2: ((0, 0, -500.0), (-(500 * WID**2 / 2 + 1000 * WID**2 / 3), 500 * L, 0), (h**2 * 1000 / 60, 0, 0)),
        3: ((0, 0, -800.0), (-800 * WID, 100 * L**2 / 2 + 200 * L**2 / 3, 0), (0, -(h**2) * 200 / 60, 0)),
    }
    coords = {n.id: np.asarray(n.p) for n in p.fem.nodes}
    for case, (f, m, hermite) in applied.items():
        rf = _field(res, "REACTION-FORCE", case)
        force = rf[:, 1:4].sum(axis=0)
        moment = sum(np.cross(coords[int(r[0])], r[1:4]) + r[4:7] for r in rf)
        # single precision SIN: 1e-6 of the case's own scale
        assert force == pytest.approx(-np.asarray(f), abs=1e-6 * np.abs(f).max()), case
        assert moment == pytest.approx(-np.asarray(m) + np.asarray(hermite), abs=1e-6 * np.abs(m).max()), case
