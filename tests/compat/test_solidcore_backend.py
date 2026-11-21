"""EXPERIMENTAL solidcore backend against pythonocc.

The single-backend invariants in ``test_backend_parity.py`` already run on solidcore (the
``backend`` fixture picks it up where ``solidcore_py`` imports). This module adds what that file
cannot: solidcore compared with pythonocc directly, and a tubular joint -- the case the prototype
was written to probe:

* brace TUB150x10 (OD 150 mm, wall 10 mm) cut by the outer cylinder of a chord TUB300x10, the
  brace at 90 and at 45 degrees to the chord;
* the same brace with a constant 45 degree single-bevel end preparation. "Constant" here means:
  in every half-plane through the brace axis the groove face runs from the root (inner wall on
  the chord surface) out through the wall at 45 degrees to the brace axis. The cutter is built by
  RULED lofts through planar quadrilateral sections, one per half-plane, so it exercises
  ``loft_profiles`` as well as booleans.

Every shape is built from the same portable verbs (make_cylinder, transform, boolean,
loft_profiles) on each kernel, in metres; solidcore sees millimetres through its scale.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ada.cad import CadBackendName, backend_available, select_backend
from ada.geom.booleans import BoolOpEnum

pytestmark = pytest.mark.solidcore

R_CHORD = 0.150  # TUB300x10 outer radius
R_O, T = 0.075, 0.010  # TUB150x10
R_I = R_O - T
BRACE_LENGTH = 0.6
CHORD_LENGTH = 1.6


@pytest.fixture(scope="module")
def occ_and_solidcore():
    missing = [n for n in ("occ", "solidcore") if not backend_available(CadBackendName(n))]
    if missing:
        pytest.skip(f"needs both kernels; missing {missing}")
    return select_backend(prefer="occ"), select_backend(prefer="solidcore")


# ------------------------------------------------------------------------------------------
# Tubular joint construction (backend-neutral)
# ------------------------------------------------------------------------------------------


def brace_frame(alpha_deg: float) -> np.ndarray:
    """4x4 placing local z along the brace, inclined alpha to the chord axis (global x)."""
    a = math.radians(alpha_deg)
    ez = np.array([math.cos(a), 0.0, math.sin(a)])
    ey = np.array([0.0, 1.0, 0.0])
    ex = np.cross(ey, ez)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = ex, ey, ez
    return m


def chord_solid(be):
    """The chord's OUTER cylinder (solid), along global x, centred on the origin."""
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = (0, 1, 0), (0, 0, 1), (1, 0, 0)
    m[:3, 3] = (-CHORD_LENGTH / 2, 0, 0)
    return be.transform(be.make_cylinder(R_CHORD, CHORD_LENGTH), m)


def brace_tube(be, alpha_deg: float):
    tube = be.boolean(BoolOpEnum.DIFFERENCE, be.make_cylinder(R_O, BRACE_LENGTH), be.make_cylinder(R_I, BRACE_LENGTH))
    return be.transform(tube, brace_frame(alpha_deg))


def brace_cut(be, alpha_deg: float):
    return be.boolean(BoolOpEnum.DIFFERENCE, brace_tube(be, alpha_deg), chord_solid(be))


def saddle_z(rho: float, phi: float, alpha_deg: float) -> float:
    """Local brace-axis height where the brace cylinder of radius rho at angle phi meets the
    chord's outer surface (the intersection on the brace's own side)."""
    a = math.radians(alpha_deg)
    y = rho * math.sin(phi)
    return (math.sqrt(R_CHORD**2 - y * y) + rho * math.cos(phi) * math.cos(a)) / math.sin(a)


def bevel_cutters(be, alpha_deg: float, n: int = 24, margin: float = 0.002):
    """Two ruled-loft solids (phi in [0, pi] and [pi, 2 pi]) removing the 45 deg single-bevel
    groove from the brace end. Each section is a planar quad in the half-plane at phi."""
    frame = brace_frame(alpha_deg)

    def world(rho, phi, z):
        p = frame @ np.array([rho * math.cos(phi), rho * math.sin(phi), z, 1.0])
        return tuple(float(c) for c in p[:3])

    def section(phi):
        zi = saddle_z(R_I, phi, alpha_deg)  # root: inner wall on the chord surface
        z_low = zi - 0.04
        quad = [
            (R_I - margin, z_low),
            (R_O + margin, z_low),
            (R_O + margin, zi + T + margin),  # the 45 deg line (axial rise == radial run)
            (R_I - margin, zi - margin),
        ]
        return [world(rho, phi, z) for rho, z in quad]

    halves = []
    for k in range(2):
        phis = [math.pi * (k + i / n) for i in range(n + 1)]
        halves.append(be.loft_profiles([section(p) for p in phis], ruled=True, solid=True))
    return halves


def brace_beveled(be, alpha_deg: float):
    shape = brace_cut(be, alpha_deg)
    for cutter in bevel_cutters(be, alpha_deg):
        shape = be.boolean(BoolOpEnum.DIFFERENCE, shape, cutter)
    return shape


# ------------------------------------------------------------------------------------------
# Primitive parity with pythonocc
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda be: be.make_box(2.0, 3.0, 4.0),
        lambda be: be.make_cylinder(0.5, 10.0),
        lambda be: be.make_sphere(1.0),
        lambda be: be.boolean(BoolOpEnum.DIFFERENCE, be.make_box(2.0, 3.0, 4.0), be.make_cylinder(0.5, 10.0)),
        lambda be: be.boolean(BoolOpEnum.UNION, be.make_box(2.0, 3.0, 4.0), be.make_cylinder(0.5, 10.0)),
        lambda be: be.boolean(BoolOpEnum.INTERSECTION, be.make_box(2.0, 3.0, 4.0), be.make_cylinder(0.5, 10.0)),
    ],
    ids=["box", "cylinder", "sphere", "cut", "union", "common"],
)
def test_volume_area_validity_agree_with_occ(occ_and_solidcore, build):
    occ, sc = occ_and_solidcore
    a, b = build(occ), build(sc)
    assert sc.is_valid(b), sc.validity_report(b)
    assert sc.volume(b) == pytest.approx(occ.volume(a), rel=1e-7)
    assert sc.area(b) == pytest.approx(occ.area(a), rel=1e-7)
    assert sc.bbox(b) == pytest.approx(occ.bbox(a), abs=1e-6)


def test_tessellation_is_a_closed_mesh_of_the_right_volume(occ_and_solidcore):
    _, sc = occ_and_solidcore
    shape = sc.boolean(BoolOpEnum.DIFFERENCE, sc.make_box(2.0, 3.0, 4.0), sc.make_cylinder(0.5, 10.0))
    m = sc.tessellate(shape, 0.001)
    p = m.positions.reshape(-1, 3).astype(float)
    t = m.indices.reshape(-1, 3)
    vol = float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6.0)
    assert vol == pytest.approx(sc.volume(shape), rel=2e-3)


def test_extruded_area_solid_build(occ_and_solidcore):
    """A TUB profile beam through ``build`` (ExtrudedAreaSolid of circle profiles)."""
    import ada

    occ, sc = occ_and_solidcore
    bm = ada.Beam("bm", (0, 0, 0), (1.0, 0.2, 0.3), "TUB150x10")
    g = bm.solid_geom()
    a, b = occ.build(g), sc.build(g)
    assert sc.is_valid(b), sc.validity_report(b)
    assert sc.volume(b) == pytest.approx(occ.volume(a), rel=1e-6)


# ------------------------------------------------------------------------------------------
# The tubular T-joint
# ------------------------------------------------------------------------------------------


def brace_cut_reference_volume(alpha_deg: float, nphi: int = 4096, nrho: int = 64) -> float:
    """The brace-cut volume by quadrature, independent of either kernel: the tube less the
    integral over its annulus of the saddle height (periodic in phi -> trapezoid rule converges
    exponentially; smooth in rho -> Gauss-Legendre). Converged to ~1e-16 relative."""
    a = math.radians(alpha_deg)
    x, w = np.polynomial.legendre.leggauss(nrho)
    rho = 0.5 * (R_O - R_I) * x + 0.5 * (R_O + R_I)
    w = 0.5 * (R_O - R_I) * w
    phi = np.linspace(0, 2 * math.pi, nphi, endpoint=False)
    p, rh = np.meshgrid(phi, rho)
    z = (np.sqrt(R_CHORD**2 - (rh * np.sin(p)) ** 2) + rh * np.cos(p) * math.cos(a)) / math.sin(a)
    removed = float((w[:, None] * rh * z).sum() * (2 * math.pi / nphi))
    return math.pi * (R_O**2 - R_I**2) * BRACE_LENGTH - removed


def occ_volume(shape, eps: float | None = 1e-9) -> float:
    """pythonocc volume with the ADAPTIVE integrator. OccBackend.volume uses the default fixed
    Gauss rule, which on these trimmed saddle faces is off by ~2.4e-5 relative."""
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    props = GProp_GProps()
    if eps is None:
        brepgprop.VolumeProperties(shape, props)
    else:
        brepgprop.VolumeProperties(shape, props, eps)
    return props.Mass()


def occ_area(shape, eps: float = 1e-9) -> float:
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    props = GProp_GProps()
    brepgprop.SurfaceProperties(shape, props, eps)
    return props.Mass()


@pytest.mark.parametrize("alpha", [90.0, 45.0])
def test_tubular_brace_cut_by_chord(occ_and_solidcore, alpha):
    occ, sc = occ_and_solidcore
    a, b = brace_cut(occ, alpha), brace_cut(sc, alpha)
    ref = brace_cut_reference_volume(alpha)
    assert occ.is_valid(a)
    assert sc.is_valid(b), sc.validity_report(b)
    # solidcore holds the saddle as an exact surface/surface intersection: it matches quadrature
    assert sc.volume(b) == pytest.approx(ref, rel=1e-10)
    # OCC approximates the intersection curve: ~1e-8 with the adaptive integrator
    assert occ_volume(a) == pytest.approx(ref, rel=1e-7)
    assert sc.area(b) == pytest.approx(occ_area(a), rel=1e-7)


@pytest.mark.parametrize("alpha", [90.0, 45.0])
def test_tubular_brace_single_bevel(occ_and_solidcore, alpha):
    occ, sc = occ_and_solidcore
    a, b = brace_beveled(occ, alpha), brace_beveled(sc, alpha)
    assert occ.is_valid(a)
    assert sc.is_valid(b), sc.validity_report(b)
    removed_occ = occ_volume(brace_cut(occ, alpha)) - occ_volume(a)
    removed_sc = sc.volume(brace_cut(sc, alpha)) - sc.volume(b)
    assert removed_occ > 0
    assert removed_sc == pytest.approx(removed_occ, rel=1e-5)
