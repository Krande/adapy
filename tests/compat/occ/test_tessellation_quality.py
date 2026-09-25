"""Configurable tessellation quality (ADA_OCC_TESS_LINEAR_DEFLECTION + angular).

The default path (ShapeTesselator, relative mesh_quality) is unchanged; setting an
absolute linear deflection switches to a BRepMesh path with explicit angular deflection
for curvature-adaptive smoothness (step2glb-like). Off by default so GLB size / mobile
perf are unaffected unless a caller opts in.

NB: BRepMesh caches the triangulation on a shape, so each measurement rebuilds a FRESH
shape (mirrors production, where every solid is meshed exactly once).

These settings belong to the OCC tessellator, so every shape is built, meshed and measured on
the OCC backend by name (``occ_backend``) -- with adacpp also installed, the active backend is
adacpp's and would never reach them. ``OccBackend.tessellate(shape)`` is
``tessellate_shape(shape, quality=1.0)``.
"""

from __future__ import annotations

import math

import numpy as np

import ada


def _fresh_cyl(be, r=0.5, h=1.0):
    return be.build(ada.PrimCyl("c", (0, 0, 0), (0, 0, h), r).solid_geom())


def _fresh_sphere(be, r=50.0):
    return be.build(ada.PrimSphere("s", (0, 0, 0), r).solid_geom())


def _area_tris(mesh):
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    fac = np.asarray(mesh.faces).reshape(-1, 3)
    cr = np.cross(pos[fac[:, 1]] - pos[fac[:, 0]], pos[fac[:, 2]] - pos[fac[:, 0]])
    return 0.5 * np.linalg.norm(cr, axis=1).sum(), len(fac)


def test_default_quality_unchanged(monkeypatch, occ_backend):
    be = occ_backend
    monkeypatch.delenv("ADA_OCC_TESS_LINEAR_DEFLECTION", raising=False)
    _, tris = _area_tris(be.tessellate(_fresh_cyl(be)))
    assert tris == 100  # the established ShapeTesselator default for this cylinder


def test_absolute_deflection_refines_curves(monkeypatch, occ_backend):
    be = occ_backend
    analytic = 2 * math.pi * 0.5 * 1.0 + 2 * math.pi * 0.5**2
    monkeypatch.delenv("ADA_OCC_TESS_LINEAR_DEFLECTION", raising=False)
    _, default_tris = _area_tris(be.tessellate(_fresh_cyl(be)))
    monkeypatch.setenv("ADA_OCC_TESS_LINEAR_DEFLECTION", "0.001")
    monkeypatch.setenv("ADA_OCC_TESS_ANGULAR_DEG", "25")
    area, tris = _area_tris(be.tessellate(_fresh_cyl(be)))
    assert tris > default_tris  # finer than the relative default
    assert area > 0.999 * analytic  # closer to the true curved-surface area
    n = np.asarray(be.tessellate(_fresh_cyl(be)).normals, dtype=float).reshape(-1, 3)
    nz = n[np.linalg.norm(n, axis=1) > 0]
    assert np.allclose(np.linalg.norm(nz, axis=1), 1.0, atol=1e-3)


def test_angular_deflection_refines_doubly_curved(monkeypatch, occ_backend):
    be = occ_backend
    # a sphere is doubly curved → the ANGULAR deflection drives its facet count
    monkeypatch.setenv("ADA_OCC_TESS_LINEAR_DEFLECTION", "10.0")
    monkeypatch.setenv("ADA_OCC_TESS_ANGULAR_DEG", "40")
    _, coarse = _area_tris(be.tessellate(_fresh_sphere(be)))
    monkeypatch.setenv("ADA_OCC_TESS_ANGULAR_DEG", "5")
    _, fine = _area_tris(be.tessellate(_fresh_sphere(be)))
    assert fine > coarse


def test_brepmesh_path_stays_within_solid_bounds(monkeypatch, occ_backend):
    be = occ_backend
    # The BRepMesh path clips phantom triangles to the solid's tight bbox (+10%): an
    # over-covered B-spline face must not "explode" the part, yet a healthy curved face
    # must not be over-clipped (area preserved). A clean sphere exercises both: every
    # vertex on its surface, no phantom, full analytic area.
    monkeypatch.setenv("ADA_OCC_TESS_LINEAR_DEFLECTION", "1.0")
    monkeypatch.setenv("ADA_OCC_TESS_ANGULAR_DEG", "15")
    r = 50.0
    shape = _fresh_sphere(be, r)
    bb = be.bbox(shape)
    bb_diag = float(np.linalg.norm(np.array(bb[3:]) - np.array(bb[:3])))
    mesh = be.tessellate(shape)
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    diag = float(np.linalg.norm(pos.max(0) - pos.min(0)))
    assert diag < bb_diag * 1.25  # within the tight bbox + clip pad — no runaway explosion
    area, _ = _area_tris(mesh)
    assert area > 0.99 * 4 * math.pi * r**2  # not over-clipped — full sphere area
