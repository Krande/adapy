"""Geometric fingerprints for topology dedup and store comparison.

Two producers build the store: the import producer preserves record identity, but
the derive producer must recognise that a corner reached from two faces is the
*same* vertex, and an arc and its chord between the same corners are *different*
edges. These keys make that decision by rounded geometry, independent of any
record id — so they also let the differ match an imported store against a derived
one whose ids differ.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

if TYPE_CHECKING:
    from ada.geom.curves import CURVE_GEOM_TYPES
    from ada.geom.surfaces import SURFACE_GEOM_TYPES


def point_key(p, nd: int) -> tuple:
    return tuple(round(float(c), nd) for c in p)


def _dir_key(d, nd: int) -> tuple:
    return tuple(round(float(c), nd) for c in d)


def curve_key(curve: CURVE_GEOM_TYPES, nd: int) -> tuple:
    """A fingerprint distinguishing two curves between the same endpoints.

    Position alone is not enough — two faces can meet at a pair of vertices yet be
    bounded by different curves between them (two arcs of a circle, an arc vs its
    chord). The type plus its defining geometry is the discriminator.
    """
    if isinstance(curve, geo_cu.SurfaceCurve):
        # a curve-on-surface IS its 3D curve for identity purposes — a store that
        # kept the surfintcur whole must still match one whose SAT round-tripped
        # through a plain exactcur of the same 3D spline.
        return curve_key(curve.curve_3d, nd)
    if isinstance(curve, geo_cu.Line):
        return ("line",)
    if isinstance(curve, geo_cu.Circle):
        pos = curve.position
        return ("circle", round(float(curve.radius), nd), _dir_key(pos.axis, nd), point_key(pos.location, nd))
    if isinstance(curve, geo_cu.Ellipse):
        pos = curve.position
        return (
            "ellipse",
            round(float(curve.semi_axis1), nd),
            round(float(curve.semi_axis2), nd),
            _dir_key(pos.axis, nd),
            point_key(pos.location, nd),
        )
    if isinstance(curve, (geo_cu.BSplineCurveWithKnots, geo_cu.RationalBSplineCurveWithKnots)):
        # Control points fingerprint the spline shape; degree/knots refine it.
        cps = tuple(point_key(cp, nd) for cp in curve.control_points_list)
        return ("bspline", int(getattr(curve, "degree", 0)), cps)
    # Fallback: type name only (keeps distinct types apart, coarse within a type).
    return (type(curve).__name__,)


def surface_key(surface: SURFACE_GEOM_TYPES, nd: int) -> tuple:
    """A fingerprint distinguishing surfaces for face comparison."""
    if isinstance(surface, geo_su.Plane):
        pos = surface.position
        return ("plane", _dir_key(pos.axis, nd), point_key(pos.location, nd))
    if isinstance(surface, geo_su.CylindricalSurface):
        pos = surface.position
        return ("cylinder", round(float(surface.radius), nd), _dir_key(pos.axis, nd), point_key(pos.location, nd))
    if isinstance(surface, (geo_su.BSplineSurfaceWithKnots, geo_su.RationalBSplineSurfaceWithKnots)):
        rows = getattr(surface, "control_points_list", None)
        if rows is not None:
            cps = tuple(point_key(cp, nd) for row in rows for cp in row)
        else:
            cps = ()
        return ("bspline_surf", cps)
    return (type(surface).__name__,)
