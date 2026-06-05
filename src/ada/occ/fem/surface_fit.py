"""Fit a B-spline surface through a structured node grid (pythonocc only).

Isolated OCC helper invoked exclusively via
``OccBackend.build_bspline_advanced_face_from_grid``. The backend-neutral surface
reconstruction (``ada.fem.formats.surface_reconstruction``) never imports this
module and never touches an OCC object — it receives a native
:class:`ada.geom.surfaces.AdvancedFace` back through ``ada.cad.active_backend()``,
so an adacpp / OCC-absent run simply falls back to flat plates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from ada.config import logger

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Face

    from ada.geom.surfaces import AdvancedFace

Grid = Sequence[Sequence[Sequence[float]]]  # (nu)(nv)(x, y, z)


def fit_advanced_face_from_grid(grid: Grid, tol: float) -> "AdvancedFace | None":
    """Fit a NURBS surface through the ``nu × nv`` node grid and return it as a
    backend-neutral :class:`~ada.geom.surfaces.AdvancedFace` (control points +
    knots serialised into ada.geom — **no OCC object escapes this function**).

    Returns ``None`` — never raises — when the grid is degenerate, the fit does
    not converge, the surface deviates from the input nodes by more than ``tol``,
    or the OCC→ada serialisation fails. The caller keeps the flat plates.
    """
    face = _fit_topods_face(grid, tol)
    if face is None:
        return None
    try:
        from ada.occ.step.geom.surfaces import occ_face_to_ada_face

        return occ_face_to_ada_face(face)
    except Exception as ex:  # serialisation failure — caller falls back
        logger.debug(f"occ_face_to_ada_face failed for fitted patch: {ex}")
        return None


def _fit_topods_face(grid: Grid, tol: float) -> "TopoDS_Face | None":
    """Approximate (or interpolate) a ``Geom_BSplineSurface`` through the grid and
    return a bounded ``TopoDS_Face``. Approximation first (bounded control net →
    small output); if its worst-case node deviation exceeds ``tol``, fall back to
    interpolation (passes through every node — faithful but larger)."""
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.GeomAbs import GeomAbs_C2
    from OCC.Core.GeomAPI import (
        GeomAPI_PointsToBSplineSurface,
        GeomAPI_ProjectPointOnSurf,
    )
    from OCC.Core.gp import gp_Pnt
    from OCC.Core.TColgp import TColgp_Array2OfPnt

    nu = len(grid)
    if nu < 2:
        return None
    nv = len(grid[0])
    if nv < 2 or any(len(row) != nv for row in grid):
        return None

    arr = TColgp_Array2OfPnt(1, nu, 1, nv)
    for i in range(nu):
        for j in range(nv):
            p = grid[i][j]
            arr.SetValue(i + 1, j + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))

    def _surface(approx: bool):
        try:
            if approx:
                algo = GeomAPI_PointsToBSplineSurface(arr, 3, 8, GeomAbs_C2, max(float(tol), 1e-7))
            else:
                algo = GeomAPI_PointsToBSplineSurface(arr)  # interpolation through points
        except Exception as ex:  # malformed grid the fitter rejects
            logger.debug(f"bspline surface fit raised: {ex}")
            return None
        if not algo.IsDone():
            return None
        return algo.Surface()

    def _max_deviation(surf) -> float:
        worst = 0.0
        for i in range(nu):
            for j in range(nv):
                p = grid[i][j]
                proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(float(p[0]), float(p[1]), float(p[2])), surf)
                if proj.NbPoints() == 0:
                    return float("inf")
                worst = max(worst, proj.LowerDistance())
        return worst

    for approx in (True, False):
        surf = _surface(approx)
        if surf is None:
            continue
        if _max_deviation(surf) > tol:
            continue
        mk = BRepBuilderAPI_MakeFace(surf, 1e-6)
        if not mk.IsDone():
            continue
        return mk.Face()
    return None
