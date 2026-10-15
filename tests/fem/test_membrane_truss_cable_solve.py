"""CalculiX and Code_Aster solve membranes, trusses and cables as adapy writes them, against closed forms.

The models are built element by element (``tests/core/fem/theory_models.py``):

* a 1 x 1 m, 2 mm square membrane of 4 x 4 elements pulled along x by 1000 N -- a uniform plane-stress state, so
  every mesh gives the exact answer: ``u_x(x = 1) = F a / (E b t)``, the Poisson contraction ``-nu`` times that across,
  and a membrane force of ``F / b``;
* two bars at 45 degrees pinned at their feet, 1 kN at the apex: each bar carries ``P / sqrt(2)`` and the apex moves
  ``P sqrt(2) / (E A)``;
* a cable and a truss in line, their joint pushed and pulled along the line: in tension both carry the load
  (``u = P L / (2 E A)``), in compression the cable goes slack and the truss carries it alone (Code_Aster only:
  CalculiX has no tension-only element);
* the square membrane pre-stretched biaxially through its edges and loaded by a pressure (Code_Aster): its centre
  deflects as the membrane equation ``N0 laplacian(w) = -p`` has it, the series solution
  ``w = 16 p a^2 / (pi^4 N0) sum (-1)^((m + n) / 2 - 1) / (m n (m^2 + n^2))``.

Measured with CalculiX 2.23 and Code_Aster 18.1.7; each leg skips without its solver. CalculiX's .frd prints six
significant digits.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from tests.core.fem.theory_models import (
    NU,
    ROD_AREA,
    E,
    cable_and_truss,
    membrane_in_tension,
    membrane_square,
    two_bar_truss,
)

import ada
from ada.fem import Bc, FemSet, FieldOutput, LoadPressure, StepImplicitStatic

SOLVERS = ("calculix", "code_aster")
#: Half a unit in the sixth significant digit of ccx's ``%12.5E``.
FRD = 5e-6


class Solved:
    def __init__(self, fem_format: str, res):
        self.fem_format = fem_format
        self.res = res
        self.coords = {
            int(i): np.asarray(c, dtype=float) for i, c in zip(res.mesh.nodes.identifiers, res.mesh.nodes.coords)
        }

    def node(self, x, y=0.0, z=0.0) -> int:
        ids = list(self.coords)
        dist = [np.linalg.norm(self.coords[i] - [x, y, z]) for i in ids]
        assert min(dist) < 1e-9
        return ids[int(np.argmin(dist))]

    def _last(self, *prefixes):
        fields = [f for f in self.res.results if any(f.name.startswith(p) or p in f.name for p in prefixes)]
        assert fields, [f.name for f in self.res.results]
        return {int(row[0]): np.asarray(row[1:], dtype=float) for row in np.asarray(fields[-1].values)}

    def u(self, node: int) -> np.ndarray:
        return self._last("DISP", "DEPL")[node]

    def field(self, *names):
        return [f for f in self.res.results if any(n in f.name for n in names)]


def _solve(a, name, fem_format, tmp_path) -> Solved:
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.utils import default_fem_res_path

    for step in a.fem.steps:
        step.add_field_output(FieldOutput("u", nodal=["U", "RF"]))
    a.to_fem(name, fem_format, scratch_dir=tmp_path, overwrite=False, execute=True, exit_on_complete=False)
    res_path = default_fem_res_path(name, scratch_dir=tmp_path, fem_format=FEATypes.from_str(fem_format))
    if fem_format == "code_aster":
        mess = (tmp_path / name / f"{name}.mess").read_text(encoding="utf-8", errors="replace")
        (verdict,) = [line for line in mess.splitlines() if "DIAGNOSTIC JOB" in line]
        assert "<S>" not in verdict and "<F>" not in verdict and "<E>" not in verdict, verdict
    return Solved(fem_format, ada.from_fem_res(res_path))


# ---- membrane in plane stress ----------------------------------------------------------------------------------


@pytest.mark.parametrize("fem_format", SOLVERS)
def test_a_membrane_in_tension_is_in_uniform_plane_stress(fem_format, require_solver, tmp_path):
    """Measured: CalculiX u_x = 2.38095e-6 m, sigma_xx = 4.9998e5 Pa (the .frd's stresses are the expanded brick's,
    extrapolated: 4e-5 low); Code_Aster u_x = 2.380952381e-6 m and SIEF_ELNO NXX = 1000 N/m -- a MEMBRANE on
    ELAS_MEMBRANE gives the membrane force per unit width, not a stress."""
    require_solver(fem_format)
    a, _, _ = membrane_in_tension()
    s = _solve(a, "memb", fem_format, tmp_path)
    t, force = 0.002, 1000.0
    eps = force / (E * 1.0 * t)
    tol = FRD if fem_format == "calculix" else 1e-9
    for y in (0.0, 0.5, 1.0):
        assert s.u(s.node(1.0, y))[0] == pytest.approx(eps, rel=tol)
    assert s.u(s.node(0.0, 1.0))[1] == pytest.approx(-NU * eps, rel=tol)
    if fem_format == "calculix":
        stress = s._last("STRESS")
        assert all(row[0] == pytest.approx(force / t, rel=1e-4) for row in stress.values())
    else:
        (sief,) = s.field("SIEF_ELNO")
        values = np.asarray(sief.values)
        assert values[..., 0] == pytest.approx(force, rel=1e-9)
        assert np.abs(values[..., 1:]).max() < 1e-6


def test_a_membrane_in_a_nonlinear_step_code_aster(require_solver, tmp_path):
    """GROT_GDEP with ELAS_MEMBRANE_SV: the same state, to the geometric nonlinearity of a 2.4e-6 strain (4e-6
    relative, measured)."""
    require_solver("code_aster")
    a, _, _ = membrane_in_tension(nl_geom=True)
    s = _solve(a, "memb_nl", "code_aster", tmp_path)
    assert s.u(s.node(1.0, 0.5))[0] == pytest.approx(1000.0 / (E * 0.002), rel=1e-5)


# ---- trusses ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fem_format", SOLVERS)
@pytest.mark.parametrize("fz", [1000.0, -1000.0])
def test_a_two_bar_truss(fem_format, fz, require_solver, tmp_path):
    """Measured: the apex moves 2.14361e-5 m (CalculiX) and 2.143610277e-5 m (Code_Aster, BARRE); each bar's axial
    force is 707.10678 N (Code_Aster SIEF_ELNO), and its stress 1.12535e6 Pa along x and along z (CalculiX, global axes;
    the closed form 1.125395e6); the supports react 500 N each way."""
    require_solver(fem_format)
    a, _, _ = two_bar_truss("truss", fz=fz)
    s = _solve(a, f"truss_{'up' if fz > 0 else 'down'}", fem_format, tmp_path)
    tol = FRD if fem_format == "calculix" else 1e-9
    assert s.u(s.node(1.0, 0.0, 1.0))[2] == pytest.approx(fz * math.sqrt(2) / (E * ROD_AREA), rel=tol)
    n_bar = fz / math.sqrt(2)
    if fem_format == "calculix":
        stress = s._last("STRESS")[s.node(0.0)]
        # The expanded brick's stresses, extrapolated to the nodes: 4e-5 low, as the membrane's.
        assert stress[0] == pytest.approx(n_bar / ROD_AREA / 2, rel=1e-4)
        assert stress[2] == pytest.approx(n_bar / ROD_AREA / 2, rel=1e-4)
    else:
        (sief,) = s.field("SIEF_ELNO")
        assert np.asarray(sief.values).ravel() == pytest.approx(n_bar, rel=1e-9)
        reac = s._last("REAC_NODA")
        assert reac[s.node(0.0)][[0, 2]] == pytest.approx([-fz / 2, -fz / 2], rel=1e-9)


# ---- cables ----------------------------------------------------------------------------------------------------


def test_a_cable_carries_tension_code_aster(require_solver, tmp_path):
    """Pulled towards the truss's support: cable and truss share the load. Measured 7.578764e-6 m against the linear
    7.578807e-6 m: CABLE is solved with large displacements (GROT_GDEP), and the difference is that (6e-6)."""
    require_solver("code_aster")
    a, _ = cable_and_truss(1000.0)
    s = _solve(a, "cable_t", "code_aster", tmp_path)
    assert s.u(s.node(1.0))[0] == pytest.approx(1000.0 * 1.0 / (2 * E * ROD_AREA), rel=2e-5)


def test_a_cable_goes_slack_in_compression_code_aster(require_solver, tmp_path):
    """Pushed the other way the cable goes slack, keeping the ``compression_ratio`` (EC_SUR_E) of its stiffness:
    ``u = P L / (E A (1 + ratio))``. Measured -1.515609805e-5 m, the closed form to 1e-9; with both members carrying
    load it would be half that."""
    require_solver("code_aster")
    ratio = 1e-4
    a, _ = cable_and_truss(-1000.0, compression_ratio=ratio)
    s = _solve(a, "cable_c", "code_aster", tmp_path)
    assert s.u(s.node(1.0))[0] == pytest.approx(-1000.0 / (E * ROD_AREA * (1 + ratio)), rel=1e-6)


def test_a_cable_pretension_is_not_a_load_in_code_aster(require_solver, tmp_path):
    """``N_INIT`` is the tension the cable's tangent stiffness starts from, not a stress the structure must carry: a
    cable pre-tensioned to 1000 N beside an unstressed truss moves the joint no more than without it (measured,
    7.5788068e-9 m under 1 N, both)."""
    require_solver("code_aster")
    a, _ = cable_and_truss(1.0, pretension=1000.0)
    s = _solve(a, "cable_pre", "code_aster", tmp_path)
    assert s.u(s.node(1.0))[0] == pytest.approx(1.0 / (2 * E * ROD_AREA), rel=1e-5)


# ---- a pressurised membrane ------------------------------------------------------------------------------------


def _membrane_series(p: float, a: float, n0: float) -> float:
    terms = range(1, 400, 2)
    s = sum((-1) ** ((m + n) // 2 - 1) / (m * n * (m * m + n * n)) for m in terms for n in terms)
    return 16 * p * a * a / (math.pi**4 * n0) * s


def test_a_pre_stretched_membrane_under_pressure_code_aster(require_solver, tmp_path):
    """A flat membrane carries pressure only through its tension. The square is stretched biaxially by 1e-5 through
    its edges (``N0 = E t eps / (1 - nu)`` = 6000 N/m), and 10 Pa presses its positive face; the step is
    geometrically nonlinear (GROT_GDEP), and ``N_INIT`` gives the first iteration the tension to start from.

    Measured on 10 x 10 elements: -1.2331e-4 m against the series' 1.2279e-4 m (+0.4 %, the mesh), downwards -- a
    positive pressure pushes into the face it names, against the +z normal."""
    require_solver("code_aster")
    n, eps, pressure = 10, 1e-5, 10.0
    n0 = E * 0.002 * eps / (1 - NU)
    a, p, nodes = membrane_square(n, n, pretension=n0)
    fem = p.fem
    edge = [nd for (i, j), nd in nodes.items() if i in (0, n) or j in (0, n)]
    fem.add_bc(Bc("edges", fem.add_set(FemSet("edges", edge, "nset")), [3]))
    for nd in edge:
        fem.add_bc(Bc(f"st{nd.id}", fem.add_set(FemSet(f"e{nd.id}", [nd], "nset")), [1, 2], [eps * nd.x, eps * nd.y]))
    step = a.fem.add_step(StepImplicitStatic("press", nl_geom=True))
    step.add_load(LoadPressure("p", pressure, fem.elsets["membrane"]))
    s = _solve(a, "cushion", "code_aster", tmp_path)
    w = s.u(s.node(0.5, 0.5))[2]
    assert w == pytest.approx(-_membrane_series(pressure, 1.0, n0), rel=0.01)
