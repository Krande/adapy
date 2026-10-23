"""An oblique circular cone closed at its apex is an exact solid that cuts and is cut.

The cone's volume is (1/3) * base area * height whatever the apex offset. Measured on a fine
tessellation of the solid: the kernels' default volume integration over rational B-spline faces
reads such a solid a few tenths of a percent high, while the surface itself is exact (the
tessellated volume converges on the analytic one).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import ada
from ada.geom import Geometry
from ada.geom import solids as geo_so

R, H = 1.0, 2.0
APEX = (0.6, 0.2, H)
CONE = math.pi * R * R * H / 3.0


def _cone(name: str) -> ada.Shape:
    return ada.Shape(name, geom=Geometry(name, geo_so.oblique_cone((0, 0, 0), (0, 0, 1), (1, 0, 0), R, APEX), None))


def _mesh_volume(backend, shape) -> float:
    mesh = backend.tessellate(shape, linear_deflection=1e-4)
    p = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    # adacpp's Mesh names its triangles `indices`, OCC's TriangleMesh `faces`.
    tris = mesh.indices if hasattr(mesh, "indices") else mesh.faces
    t = np.asarray(tris, dtype=int).reshape(-1, 3)
    return float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6.0)


def test_the_oblique_cone_has_the_cone_volume_and_reaches_its_apex(backend):
    shape = backend.build(_cone("cone").solid_geom())
    assert _mesh_volume(backend, shape) == pytest.approx(CONE, rel=2e-4)
    x0, y0, z0, x1, y1, z1 = backend.bbox(shape)
    assert (z0, z1) == pytest.approx((0.0, H), abs=1e-6)
    assert (x0, x1, y0, y1) == pytest.approx((-R, R, -R, R), abs=1e-6)


def test_the_oblique_cone_takes_a_bore(backend):
    cone = _cone("cone")
    # A bore of r 0.25 up the base normal to z 0.5: inside the cone all the way (the cone's
    # section at z is a circle of R (1 - z/H) about the point (z/H) * apex).
    cone.add_boolean(ada.PrimCyl("bore", (0, 0, -1), (0, 0, 0.5), 0.25))
    bored = _mesh_volume(backend, backend.build(cone.solid_geom()))
    assert bored == pytest.approx(CONE - math.pi * 0.25**2 * 0.5, rel=2e-4)


def test_the_oblique_cone_cuts_as_a_solid(backend):
    box = ada.PrimBox("box", (-2, -2, -1), (2, 2, 3))
    box.add_boolean(_cone("cutter"))
    assert _mesh_volume(backend, backend.build(box.solid_geom())) == pytest.approx(64.0 - CONE, rel=2e-5)


def test_an_apex_in_the_base_plane_is_refused():
    with pytest.raises(ValueError):
        geo_so.oblique_cone((0, 0, 0), (0, 0, 1), (1, 0, 0), R, (0.5, 0.0, 0.0))
