"""Reconstruct a :class:`Section` from an explicit cross-section polyline.

This is the fallback used when importing a profile that carries no adapy
parameters (foreign IFC), e.g. an ``IfcArbitraryClosedProfileDef`` /
``IfcArbitraryProfileDefWithVoids``. We try to recognise a parametric section
(box, flatbar, I, T) from the geometry; if nothing matches confidently we keep
the faithful geometry as a POLY section.

The recognisers are deliberately **order- and winding-independent**: they group
vertices by axis-aligned levels and reason about the bounding box, never about the
sequence the points happen to appear in.
"""

from __future__ import annotations

from ada.base.units import Units
from ada.config import Config
from ada.sections.categories import BaseTypes

Point2D = tuple[float, float]


def section_from_polyline(
    outer_pts: list[Point2D],
    inner_pts: list[Point2D] | None = None,
    name: str | None = None,
    units: Units = Units.M,
    tol: float | None = None,
):
    """Return a :class:`Section` reconstructed from 2D cross-section curves.

    Tries parametric recognition first and falls back to a POLY section that
    preserves the exact geometry. Never returns ``None``.
    """
    from ada.sections.concept import Section

    if tol is None:
        tol = Config().general_point_tol

    outer = _dedup_closing(outer_pts, tol)
    inner = _dedup_closing(inner_pts, tol) if inner_pts else None

    sec = _recognize_parametric(name, outer, inner, units, tol)
    if sec is not None:
        return sec

    # Fallback: keep the geometry as-is.
    return Section(
        name=name or "PolyCurve",
        sec_type=BaseTypes.POLY,
        outer_poly=_as_curve_poly(outer),
        inner_poly=_as_curve_poly(inner) if inner else None,
        units=units,
    )


def _recognize_parametric(name, outer, inner, units, tol):
    from ada.sections.concept import Section

    rect = _axis_rectangle(outer, tol)

    # Hollow rectangle with one rectangular void -> BOX.
    if rect is not None and inner is not None:
        irect = _axis_rectangle(inner, tol)
        if irect is not None and _rect_inside(rect, irect, tol):
            oxmin, oxmax, oymin, oymax = rect
            ixmin, ixmax, iymin, iymax = irect
            t_w = ((ixmin - oxmin) + (oxmax - ixmax)) / 2.0
            return Section(
                name=name,
                sec_type=BaseTypes.BOX,
                h=oymax - oymin,
                w_top=oxmax - oxmin,
                w_btn=oxmax - oxmin,
                t_w=t_w,
                t_ftop=oymax - iymax,
                t_fbtn=iymin - oymin,
                units=units,
            )

    # Solid rectangle, no void -> FLATBAR.
    if rect is not None and inner is None:
        oxmin, oxmax, oymin, oymax = rect
        return Section(
            name=name,
            sec_type=BaseTypes.FLATBAR,
            h=oymax - oymin,
            w_top=oxmax - oxmin,
            w_btn=oxmax - oxmin,
            units=units,
        )

    # Open profiles (I/T) are described by a single outer curve with no void.
    if inner is None:
        sec = _recognize_i_or_t(name, outer, units, tol)
        if sec is not None:
            return sec

    return None


def _recognize_i_or_t(name, outer, units, tol):
    """Recognise a symmetric I- or T-profile from its outer silhouette."""
    from ada.sections.concept import Section

    levels = _group_by_level(outer, axis=1, tol=tol)  # group by y
    ys = sorted(levels)
    counts = [len(levels[y]) for y in ys]

    # I-profile: 4 y-levels, vertex counts (top->bottom) 2,4,4,2.
    if len(ys) == 4 and counts == [2, 4, 4, 2]:
        ybtn, y_lo, y_hi, ytop = ys
        h = ytop - ybtn
        w_top = _x_span(levels[ytop])
        w_btn = _x_span(levels[ybtn])
        t_ftop = ytop - y_hi
        t_fbtn = y_lo - ybtn
        t_w = _inner_x_span(levels[y_hi])
        if not _is_symmetric(outer, tol):
            return None
        return Section(
            name=name,
            sec_type=BaseTypes.IPROFILE,
            h=h,
            w_top=w_top,
            w_btn=w_btn,
            t_w=t_w,
            t_ftop=t_ftop,
            t_fbtn=t_fbtn,
            units=units,
        )

    # T-profile: 3 y-levels, vertex counts (top->bottom) 2,4,2.
    if len(ys) == 3 and counts == [2, 4, 2]:
        ybtn, y_mid, ytop = ys
        if not _is_symmetric(outer, tol):
            return None
        # Collapsed-bottom-flange TPROFILE encoding (w_btn = t_w, t_fbtn = t_ftop)
        # — matches string_to_section; None here breaks the section writers'
        # arithmetic (Genie XML unsymmetrical_i_section).
        return Section(
            name=name,
            sec_type=BaseTypes.TPROFILE,
            h=ytop - ybtn,
            w_top=_x_span(levels[ytop]),
            w_btn=_x_span(levels[ybtn]),
            t_w=_x_span(levels[ybtn]),
            t_ftop=ytop - y_mid,
            t_fbtn=ytop - y_mid,
            units=units,
        )

    return None


# --- geometry helpers (all order/winding independent) -----------------------


def _dedup_closing(pts, tol):
    """Drop a trailing point that merely repeats the first (closing vertex)."""
    out = [tuple(float(c) for c in (p[0], p[1])) for p in pts]
    if len(out) > 1 and _close(out[0], out[-1], tol):
        out = out[:-1]
    return out


def _close(a, b, tol):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _axis_rectangle(pts, tol):
    """If ``pts`` is an axis-aligned rectangle, return (xmin, xmax, ymin, ymax)."""
    uniq = _unique(pts, tol)
    if len(uniq) != 4:
        return None
    xmin, xmax, ymin, ymax = _bbox(uniq)
    if xmax - xmin <= tol or ymax - ymin <= tol:
        return None
    corners = [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]
    for corner in corners:
        if not any(_close(corner, p, tol) for p in uniq):
            return None
    return xmin, xmax, ymin, ymax


def _rect_inside(outer, inner, tol):
    oxmin, oxmax, oymin, oymax = outer
    ixmin, ixmax, iymin, iymax = inner
    return ixmin > oxmin - tol and ixmax < oxmax + tol and iymin > oymin - tol and iymax < oymax + tol


def _unique(pts, tol):
    uniq = []
    for p in pts:
        if not any(_close(p, q, tol) for q in uniq):
            uniq.append(p)
    return uniq


def _group_by_level(pts, axis, tol):
    """Group points into buckets sharing the same coordinate on ``axis``."""
    levels: dict[float, list] = {}
    for p in _unique(pts, tol):
        key = next((k for k in levels if abs(k - p[axis]) <= tol), None)
        if key is None:
            levels[p[axis]] = [p]
        else:
            levels[key].append(p)
    return levels


def _x_span(level_pts):
    xs = [p[0] for p in level_pts]
    return max(xs) - min(xs)


def _inner_x_span(level_pts):
    """Span between the two innermost (closest to centre) x-values in a level."""
    xs = sorted(p[0] for p in level_pts)
    mid = (xs[0] + xs[-1]) / 2.0
    left = max((x for x in xs if x <= mid), default=mid)
    right = min((x for x in xs if x >= mid), default=mid)
    return right - left


def _is_symmetric(pts, tol):
    """True if the point cloud is mirror-symmetric about its mid x."""
    xmin, xmax, _, _ = _bbox(pts)
    midx = (xmin + xmax) / 2.0
    uniq = _unique(pts, tol)
    for p in uniq:
        mirror = (2 * midx - p[0], p[1])
        if not any(_close(mirror, q, tol) for q in uniq):
            return False
    return True


def _as_curve_poly(pts):
    from ada.api.curves import CurvePoly2d

    return CurvePoly2d(pts, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
