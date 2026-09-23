"""A complete sphere (bounded only by its closure seam) must tessellate, not render blank.

A full sphere arrives as a SphericalSurface face whose only boundary is the great-circle
seam through the poles. Building a face from that degenerate wire yields 0 triangles
(built-but-unmeshed). The build must detect this and use the surface's natural bounds.
"""

from __future__ import annotations

import numpy as np

import ada.geom.curves as gc
import ada.geom.surfaces as gs
from ada.geom.placement import Axis2Placement3D


def _full_sphere_face(radius=1.0, center=(0.0, 0.0, 0.0)) -> gs.AdvancedFace:
    sphere = gs.SphericalSurface(position=Axis2Placement3D(location=center), radius=radius)
    # seam = great circle in the x=0 plane (normal +X), poles at +/-Z
    seam = gc.Circle(position=Axis2Placement3D(location=center, axis=(1, 0, 0), ref_direction=(0, 0, 1)), radius=radius)
    pole_n = (center[0], center[1], center[2] + radius)
    pole_s = (center[0], center[1], center[2] - radius)

    def _arc(start, end):
        ec = gc.EdgeCurve(start=start, end=end, edge_geometry=seam, same_sense=True)
        return gc.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)

    loop = gc.EdgeLoop(edge_list=[_arc(pole_n, pole_s), _arc(pole_s, pole_n)])
    return gs.AdvancedFace(bounds=[gs.FaceBound(bound=loop, orientation=True)], face_surface=sphere, same_sense=True)


def _occ():
    # make_face_from_geom is the OCC builder, so its faces are meshed on that backend.
    from ada.cad import select_backend

    return select_backend(prefer="occ")


def _ntris(face) -> int:
    # The render path: OccBackend.tessellate. (Its TriangleMesh names the index buffer
    # ``faces``, not the Mesh protocol's ``indices``.)
    return len(np.asarray(_occ().tessellate(face, 0.05).faces).reshape(-1, 3))


def test_full_sphere_seam_detected():
    from ada.occ.geom.surfaces import (
        _is_full_sphere_seam,
        make_spherical_surface_from_geom,
    )

    af = _full_sphere_face(radius=2.0)
    surf = make_spherical_surface_from_geom(af.face_surface)
    assert _is_full_sphere_seam(af, surf) is True


def test_spherical_cap_is_not_a_seam():
    # a cap bounded by a smaller circle (radius < sphere radius) must NOT be treated as a
    # full-sphere seam — it keeps the normal wire path
    from ada.occ.geom.surfaces import (
        _is_full_sphere_seam,
        make_spherical_surface_from_geom,
    )

    af = _full_sphere_face(radius=2.0)
    af.bounds[0].bound.edge_list[0].edge_element.edge_geometry.radius = 1.0  # smaller trim circle
    surf = make_spherical_surface_from_geom(af.face_surface)
    assert _is_full_sphere_seam(af, surf) is False


def test_full_sphere_tessellates():
    from ada.occ.geom.surfaces import make_face_from_geom

    face = make_face_from_geom(_full_sphere_face(radius=2.0))
    assert face is not None and _occ().shape_type(face) == "face"
    assert _ntris(face) > 0, "full sphere must tessellate, not render blank"
