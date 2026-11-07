"""Point masses, rotary inertias, non-structural masses and springs (``Mass``, ``Spring``), solved against closed forms
in Code_Aster (and Sestra, where installed and the case is one it takes).

Each layout error shows as a wrong number: every symmetric matrix Code_Aster takes is its upper triangle column by
column (write_masses), so the anisotropic mass, the coupled inertia tensor and the coupled two-node link below would
all solve to other frequencies if one were given row by row.

* **A mass on a grounded spring** (SPRING1, 3 x 3): ``f_i = sqrt(k_i / m_i) / 2 pi`` along each axis, isotropic and
  anisotropic mass.
* **A rotary inertia on a grounded rotational spring** (SPRING1 6 x 6 + ROTARYI with products of inertia): the
  frequencies of ``K_r v = w^2 I v``.
* **A two-node spring** (SPRING2) from a held node to a mass, one link coupling x of the first node to y of the second:
  the free node's block of the assembled 12 x 12.
* **A cantilever on a rotational spring** (static): ``u = P L^3 / (3 E I) + P L^2 / k_theta``.
* **A non-structural mass** on a simply supported beam: ``f_1 = pi / (2 L^2) sqrt(E I / (rho A + mu))``, ``mu`` the
  added mass per length, given as a total and per length (Code_Aster's MASS_REP).
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.linalg

import ada
from ada.fem import Bc, FemSet, LoadPoint, Mass, Spring, StepEigen, StepImplicitStatic
from ada.fem.formats.sesam.write.write_springs import two_node_matrix
from ada.materials.metals import CarbonSteel

TWO_PI = 2 * np.pi


def _frequencies(res) -> np.ndarray:
    return np.array(sorted(m.f_hz for m in res.get_eig_summary().modes if m.f_hz is not None), dtype=float)


def _point_model(name="P"):
    """A part holding one node at the origin (and its FEM), for discretes alone."""
    p = ada.Part(name)
    node = p.fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    return p, p.fem, node


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
@pytest.mark.parametrize("mass", [250.0, (100.0, 200.0, 400.0)], ids=["isotropic", "anisotropic"])
def test_a_mass_on_a_grounded_spring_vibrates_at_root_k_over_m(tmp_path, require_solver, solver, mass):
    require_solver(solver)
    p, fem, node = _point_model()
    k = np.array([1.0e6, 3.0e6, 9.0e6])
    # the rotations held stiffly too: the node is a 6-dof one in Sestra (its GMAS mass element), with no inertia there
    stiff = np.diag([*k, 1.0e9, 1.0e9, 1.0e9])
    fem.add_spring(Spring("spr", 1, "SPRING1", stiff, fem.add_set(FemSet("spr_n", [node], "nset"))))
    ptype = Mass.PTYPES.ANISOTROPIC if isinstance(mass, tuple) else None
    fem.add_mass(Mass("m", [node], list(mass) if ptype else mass, ptype=ptype, mass_id=2))
    # an inertia for the stiff rotations, or the eigenproblem has massless dofs (ARPACK stops in both solvers); their
    # modes, sqrt(1e9 / 1) / 2 pi = 5 kHz, are far above the three asked for
    fem.add_mass(Mass("rot", [node], [1.0, 1.0, 1.0, 0.0, 0.0, 0.0], mass_type="ROTARYI", mass_id=3))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=3))
    res = a.to_fem("msp", solver, scratch_dir=tmp_path, overwrite=True, execute=True)
    m = np.broadcast_to(np.asarray(mass, float), 3)
    want = np.sort(np.sqrt(k / m) / TWO_PI)
    assert _frequencies(res)[:3] == pytest.approx(want, rel=1e-6 if solver == "code_aster" else 1e-5)


@pytest.mark.parametrize("solver", ["code_aster"])
def test_a_rotary_inertia_on_a_rotational_spring_vibrates_at_the_generalised_eigenvalues(
    tmp_path, require_solver, solver
):
    """Code_Aster only: the Sesam writer keeps I11, I22, I33 alone (BNMASS has no products of inertia)."""
    require_solver(solver)
    p, fem, node = _point_model()
    kt, kr = 1.0e9, np.array([2.0e5, 5.0e5, 9.0e5])
    fem.add_spring(
        Spring("spr", 1, "SPRING1", np.diag([kt, kt, kt, *kr]), fem.add_set(FemSet("spr_n", [node], "nset")))
    )
    m = 10.0
    fem.add_mass(Mass("m", [node], m, mass_id=2))
    inertia = [4.0, 6.0, 9.0, 1.0, 0.5, -1.5]  # I11, I22, I33, I12, I13, I23
    fem.add_mass(Mass("rot", [node], inertia, mass_type="ROTARYI", mass_id=3))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=6))
    res = a.to_fem("rot", solver, scratch_dir=tmp_path, overwrite=True, execute=True)
    i11, i22, i33, i12, i13, i23 = inertia
    tensor = np.array([[i11, i12, i13], [i12, i22, i23], [i13, i23, i33]])
    rotational = np.sqrt(scipy.linalg.eigh(np.diag(kr), tensor, eigvals_only=True)) / TWO_PI
    translational = np.full(3, np.sqrt(kt / m) / TWO_PI)
    want = np.sort(np.concatenate([rotational, translational]))
    assert _frequencies(res)[:6] == pytest.approx(want, rel=1e-6)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_two_node_spring_holds_a_mass_with_its_links_stiffness(tmp_path, require_solver, solver):
    require_solver(solver)
    p = ada.Part("P")
    fem = p.fem
    held = fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    free = fem.nodes.add(ada.Node((1.0, 0.0, 0.0), 2))
    links = np.zeros((6, 6))
    links[0, 0], links[1, 1], links[2, 2] = 2.0e6, 5.0e6, 8.0e6
    links[0, 1] = 1.5e6  # x of the held node to y of the free one
    links[3, 3] = links[4, 4] = links[5, 5] = 1.0e9  # the free node's rotations held stiffly by the link
    fem.add_spring(Spring("link", 1, "SPRING2", links, fem.add_set(FemSet("link_n", [held, free], "nset"))))
    m = 50.0
    fem.add_mass(Mass("m", [free], m, mass_id=2))
    fem.add_mass(Mass("rot", [free], [1.0, 1.0, 1.0, 0.0, 0.0, 0.0], mass_type="ROTARYI", mass_id=3))  # 5 kHz modes
    fem.add_bc(Bc("hold", fem.add_set(FemSet("held", [held], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=3))
    res = a.to_fem("link", solver, scratch_dir=tmp_path, overwrite=True, execute=True)
    k_free = two_node_matrix(links)[6:9, 6:9]
    want = np.sort(np.sqrt(np.linalg.eigvalsh(k_free) / m) / TWO_PI)
    assert _frequencies(res)[:3] == pytest.approx(want, rel=1e-6 if solver == "code_aster" else 1e-5)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_cantilever_on_a_rotational_spring_deflects_by_its_bending_plus_its_root_turn(
    tmp_path, require_solver, solver
):
    require_solver(solver)
    mat = ada.Material("S355", CarbonSteel("S355"))
    L, P, k_theta = 3.0, 1.0e4, 2.0e7
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    # translations and the other rotations held; the turn about z resisted by the spring alone
    fem.add_bc(Bc("root", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5]))
    big = 1.0e12  # (Sestra wants element ids 1..n: the spring takes the next one)
    fem.add_spring(
        Spring(
            "hinge",
            int(fem.elements.max_el_id) + 1,
            "SPRING1",
            np.diag([big, big, big, big, big, k_theta]),
            fem.add_set(FemSet("hinge_n", [root], "nset")),
        )
    )
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("tip"))
    step.add_load(LoadPoint("P", P, fem.add_set(FemSet("tip", [tip], "nset")), 2))
    res = a.to_fem("hinge", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = res.case_fields("nodal_displacement").items()
    (disp,) = fields
    u = {int(r[0]): r[1:7] for r in np.asarray(disp.values, float)}
    props = bm.section.properties
    iz = min(props.Iy, props.Iz)
    want = P * L**3 / (3 * mat.model.E * iz) + P * L**2 / k_theta
    if solver == "sesam":
        # Sestra's beam is shear-deformable: its shear deflection P L / (G As), As the shear area along y (SHARY)
        want += P * L / (mat.model.E / (2 * (1 + mat.model.v)) * props.Shary)
    assert u[int(tip.id)][1] == pytest.approx(want, rel=1e-6 if solver == "code_aster" else 1e-5)


@pytest.mark.parametrize("units", ["TOTAL", "PER UNIT LENGTH"])
def test_a_non_structural_mass_lowers_a_beams_frequency_as_its_added_mass_per_length(tmp_path, require_solver, units):
    """Code_Aster's MASS_REP spreads it over the beam's nodes by the length around each: a lumped mass on a
    consistent-mass beam, so the first mode is within the mesh's own error (L / h = 40)."""
    require_solver("code_aster")
    mat = ada.Material("S355", CarbonSteel("S355"))
    L = 10.0
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (left,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (right,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("pin", fem.add_set(FemSet("pin", [left], "nset")), [1, 2, 3, 4]))
    fem.add_bc(Bc("roll", fem.add_set(FemSet("roll", [right], "nset")), [2, 3]))
    props = bm.section.properties
    rho_a = mat.model.rho * props.Ax
    mu = rho_a  # doubles the mass per length: the frequency drops by sqrt(2)
    region = fem.add_set(FemSet("all_beams", list(fem.elements.lines), "elset"))
    value = mu * L if units == "TOTAL" else mu
    fem.add_mass(Mass("ns", region, value, mass_type="NONSTRUCTURAL", units=units))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=4))
    res = a.to_fem("ns", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    iz = min(props.Iy, props.Iz)
    want = np.pi / (2 * L**2) * np.sqrt(mat.model.E * iz / (rho_a + mu))
    assert _frequencies(res)[0] == pytest.approx(want, rel=2e-3)
