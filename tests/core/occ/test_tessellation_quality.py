"""Configurable tessellation quality (ADA_TESS_LINEAR_DEFLECTION + angular).

The default path (ShapeTesselator, relative mesh_quality) is unchanged; setting an
absolute linear deflection switches to a BRepMesh path with explicit angular deflection
for curvature-adaptive smoothness (step2glb-like). Off by default so GLB size / mobile
perf are unaffected unless a caller opts in.

NB: BRepMesh caches the triangulation on a shape, so each measurement rebuilds a FRESH
shape (mirrors production, where every solid is meshed exactly once).
"""

from __future__ import annotations

import math

import numpy as np

import ada
from ada.cad import active_backend
from ada.occ.tessellating import tessellate_shape


def _fresh_cyl(r=0.5, h=1.0):
    return active_backend().build(ada.PrimCyl("c", (0, 0, 0), (0, 0, h), r).solid_geom())


def _fresh_sphere(r=50.0):
    return active_backend().build(ada.PrimSphere("s", (0, 0, 0), r).solid_geom())


def _area_tris(mesh):
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    fac = np.asarray(mesh.faces).reshape(-1, 3)
    cr = np.cross(pos[fac[:, 1]] - pos[fac[:, 0]], pos[fac[:, 2]] - pos[fac[:, 0]])
    return 0.5 * np.linalg.norm(cr, axis=1).sum(), len(fac)


def test_default_quality_unchanged(monkeypatch):
    monkeypatch.delenv("ADA_TESS_LINEAR_DEFLECTION", raising=False)
    _, tris = _area_tris(tessellate_shape(_fresh_cyl(), quality=1.0))
    assert tris == 100  # the established ShapeTesselator default for this cylinder


def test_absolute_deflection_refines_curves(monkeypatch):
    analytic = 2 * math.pi * 0.5 * 1.0 + 2 * math.pi * 0.5**2
    monkeypatch.delenv("ADA_TESS_LINEAR_DEFLECTION", raising=False)
    _, default_tris = _area_tris(tessellate_shape(_fresh_cyl(), quality=1.0))
    monkeypatch.setenv("ADA_TESS_LINEAR_DEFLECTION", "0.001")
    monkeypatch.setenv("ADA_TESS_ANGULAR_DEG", "25")
    area, tris = _area_tris(tessellate_shape(_fresh_cyl(), quality=1.0))
    assert tris > default_tris  # finer than the relative default
    assert area > 0.999 * analytic  # closer to the true curved-surface area
    n = np.asarray(tessellate_shape(_fresh_cyl(), quality=1.0).normals, dtype=float).reshape(-1, 3)
    nz = n[np.linalg.norm(n, axis=1) > 0]
    assert np.allclose(np.linalg.norm(nz, axis=1), 1.0, atol=1e-3)


def test_angular_deflection_refines_doubly_curved(monkeypatch):
    # a sphere is doubly curved → the ANGULAR deflection drives its facet count
    monkeypatch.setenv("ADA_TESS_LINEAR_DEFLECTION", "10.0")
    monkeypatch.setenv("ADA_TESS_ANGULAR_DEG", "40")
    _, coarse = _area_tris(tessellate_shape(_fresh_sphere(), quality=1.0))
    monkeypatch.setenv("ADA_TESS_ANGULAR_DEG", "5")
    _, fine = _area_tris(tessellate_shape(_fresh_sphere(), quality=1.0))
    assert fine > coarse
