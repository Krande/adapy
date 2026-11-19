"""A beam's section outline and booleans as small JSON values, and back.

The viewer's per-beam metadata (:mod:`ada.comms.msg_handling.object_metadata`) names a section and gives its
dimensions. That is enough for a parametric section, but a POLY section (a section given only as an outline) and
a beam's booleans (the cuts made in it) are only geometry. This module writes both as compact, diffable values and
reads them back, so a beam described by its metadata can be made again as it was:

* an outline is a list of points ``[x, y]`` in the section's own frame, a point with a fillet radius
  ``[x, y, r]`` (as :class:`ada.api.curves.CurvePoly2d` takes them);
* a section dict gains ``outer`` (and ``inner``, the one void ada's ``Section`` holds) for a POLY section;
* a boolean is ``{"kind": "box" | "cyl" | "cone" | "sphere" | "extrude" | "revolve" | "half_space", ...}`` with
  the primitive's own parameters, in the same frame as the beam's end points. ``op`` is written only when it is
  not a cut (``"union"``, ``"intersection"``), ``place`` only when the primitive has a placement of its own. A
  primitive this module cannot describe is written as ``{"kind": "<class name>", "note": "..."}`` so that it is
  seen, not dropped.

Numbers are rounded (lengths to :data:`NDIGITS` decimals, directions to :data:`DIR_NDIGITS`), and a whole number is
written without its ``.0``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada import Beam, Boolean, Section
    from ada.api.curves import CurvePoly2d

#: Decimals a length is written with: 4 is a tenth of a millimetre in metres.
NDIGITS = 4
#: Decimals a direction's component is written with.
DIR_NDIGITS = 6

_KIND_OF_OP = {"DIFFERENCE": None, "UNION": "union", "INTERSECTION": "intersection"}


def num(value, ndigits: int = NDIGITS) -> float | int:
    """``value`` rounded, an int where it is whole (``0.3`` not ``0.30000000000000004``, ``2`` not ``2.0``)."""
    x = round(float(value), ndigits)
    if x == 0:
        return 0
    return int(x) if x.is_integer() else x


def vec(values: Iterable, ndigits: int = NDIGITS) -> list:
    return [num(v, ndigits) for v in values]


# ---- outlines -----------------------------------------------------------------------------------------------------


def _line_intersection(p1, d1, p2, d2):
    """Where the line through ``p1`` along ``d1`` meets the one through ``p2`` along ``d2``; None when parallel."""
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return p1[0] + t * d1[0], p1[1] + t * d1[1]


def _fillet_corner(arc, prev_seg, next_seg, tol: float = 1e-6):
    """``(corner, r)`` for an arc that rounds the corner between its two neighbouring lines (tangent to both), as
    the corner point and radius ada takes; None for any other arc."""
    from ada.api.curves import ArcSegment

    if isinstance(prev_seg, ArcSegment) or isinstance(next_seg, ArcSegment):
        return None
    a_start, a_end = np.asarray(arc.p1, float)[:2], np.asarray(arc.p2, float)[:2]
    d_in = np.asarray(prev_seg.p2, float)[:2] - np.asarray(prev_seg.p1, float)[:2]
    d_out = np.asarray(next_seg.p2, float)[:2] - np.asarray(next_seg.p1, float)[:2]
    if np.linalg.norm(d_in) < tol or np.linalg.norm(d_out) < tol:
        return None
    corner = _line_intersection(a_start, d_in, a_end, d_out)
    if corner is None:
        return None
    corner = np.asarray(corner)
    # Tangent to both: the corner is as far from either end of the arc (a fillet's two tangent lengths).
    if abs(np.linalg.norm(corner - a_start) - np.linalg.norm(corner - a_end)) > max(tol, 1e-4 * np.linalg.norm(d_in)):
        return None
    r = getattr(arc, "radius", None)
    if r is None:
        mid = getattr(arc, "midpoint", None)
        if mid is None:
            return None
        from ada.comms.msg_handling.object_metadata import _circle_through

        circle = _circle_through(tuple(a_start), tuple(np.asarray(mid, float)[:2]), tuple(a_end))
        if circle is None:
            return None
        r = circle[1]
    return (float(corner[0]), float(corner[1])), float(r)


def _points_from_segments(segments) -> list[tuple[float, float, float | None]]:
    """A loop of line and arc segments as corner points with fillet radii. An arc that is not a fillet between
    two lines is kept as its start and middle points (a polygon through it)."""
    from ada.api.curves import ArcSegment

    n = len(segments)
    corners = {}
    for i, seg in enumerate(segments):
        if isinstance(seg, ArcSegment):
            corners[i] = _fillet_corner(seg, segments[i - 1], segments[(i + 1) % n])
    out = []
    for i, seg in enumerate(segments):
        if isinstance(seg, ArcSegment):
            found = corners[i]
            if found is not None:
                (x, y), r = found
                out.append((x, y, r))
            else:
                out.append((float(seg.p1[0]), float(seg.p1[1]), None))
                mid = getattr(seg, "midpoint", None)
                if mid is not None:
                    out.append((float(mid[0]), float(mid[1]), None))
            continue
        if corners.get((i - 1) % n) is not None:
            continue  # its start is the end of a fillet: the fillet's corner stands for it
        out.append((float(seg.p1[0]), float(seg.p1[1]), None))
    return out


def _in_reading_order(points: list) -> list:
    """``points`` in the order that ``CurvePoly2d`` keeps them as given: it turns a loop it reads as not clockwise
    (keeping the first point), so a loop that it would turn is written turned already. Written again, a curve
    read from the list gives the same list."""
    from ada.core.vector_utils import is_clockwise

    if not points or is_clockwise(points):
        return points
    turned = [points[0]] + list(reversed(points[1:]))
    return turned if not is_clockwise(turned) else points


def outline_to_list(curve: CurvePoly2d | None, ndigits: int = NDIGITS) -> list[list] | None:
    """A closed 2D outline as ``[[x, y], [x, y, r], ...]``: its points in its own plane, ``r`` where a point's
    corner is rounded. None for no curve.

    A curve made from points (with radii) is written as its points. A curve made from segments
    (``CurvePoly2d.from_segments``), whose arcs are not radii of its points, has each arc that rounds a corner
    between two lines written as that corner and its radius."""
    from ada.api.curves import ArcSegment

    if curve is None:
        return None
    radii = dict(getattr(curve, "radiis", None) or {})
    segments = getattr(curve, "_segments", None)  # built only for a curve made from segments, or once drawn
    if not radii and segments and any(isinstance(s, ArcSegment) for s in segments):
        points = _points_from_segments(segments)
    else:
        points = [(float(p[0]), float(p[1]), radii.get(i)) for i, p in enumerate(curve.points2d)]
    points = _in_reading_order(points)
    out = []
    for x, y, r in points:
        item = [num(x, ndigits), num(y, ndigits)]
        if r:
            rr = num(r, ndigits)
            if rr:
                item.append(rr)
        out.append(item)
    return out


def outline_from_list(points: list, origin=(0, 0, 0), normal=(0, 0, 1), xdir=(1, 0, 0)) -> CurvePoly2d:
    """The curve :func:`outline_to_list` wrote, in the section's own plane unless ``origin``, ``normal`` and
    ``xdir`` say another."""
    from ada.api.curves import CurvePoly2d

    pts = [tuple(float(c) for c in p[:3]) for p in points]
    if len(pts) < 3:
        raise ValueError(f"an outline needs at least three points; got {len(pts)}")
    return CurvePoly2d(pts, origin=tuple(origin), xdir=tuple(xdir), normal=tuple(normal))


# ---- sections -----------------------------------------------------------------------------------------------------


def _type_name(section) -> str | None:
    sec_type = getattr(section, "type", None)
    if sec_type is None:
        return None
    return str(getattr(sec_type, "value", sec_type))


def section_outline(section: Section | None, ndigits: int = NDIGITS) -> dict:
    """``{"outer": [...], "inner": [...]}`` for a POLY section (``inner`` only where it has a void); empty for
    any other section, which its name and dimensions describe."""
    if section is None or str(_type_name(section) or "").upper() != "POLY":
        return {}
    out = {}
    try:
        outer = outline_to_list(getattr(section, "poly_outer", None), ndigits)
        if outer:
            out["outer"] = outer
            inner = outline_to_list(getattr(section, "poly_inner", None), ndigits)
            if inner:
                out["inner"] = inner
    except Exception as exc:  # noqa: BLE001 - metadata never fails a scene for what it cannot say
        logger.debug(f"member_dict: the outline of section {getattr(section, 'name', None)!r} is not written: {exc}")
        return {}
    return out


_PARAMETRIC_KEYS = ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "r_inner", "wt")


def section_from_dict(d: dict, units="m") -> Section:
    """The section a section dict describes: a POLY from its ``outer`` (and ``inner``) outline, else a section of
    its ``type`` with the dimensions it gives, else the section ada makes from its ``name``."""
    from ada import Section

    name = str(d.get("name") or "")
    kind = str(d.get("type") or "").upper()
    if d.get("outer"):
        inner = d.get("inner")
        return Section(
            name or "PolyCurve",
            "POLY",
            outer_poly=outline_from_list(d["outer"]),
            inner_poly=outline_from_list(inner) if inner else None,
            units=units,
        )
    dims = {k: float(d[k]) for k in _PARAMETRIC_KEYS if isinstance(d.get(k), (int, float))}
    if kind and kind != "POLY" and dims:
        return Section(name or kind, kind, units=units, **dims)
    if name:
        made = Section.from_str(name)
        return made[0] if isinstance(made, list) else made
    raise ValueError(f"a section needs an outline, a type with dimensions, or a name; got {d}")


# ---- booleans -----------------------------------------------------------------------------------------------------


def _place_dict(prim) -> dict:
    place = getattr(prim, "placement", None)
    if place is None:
        return {}
    try:
        if place.is_identity(use_absolute_placement=False):
            return {}
    except Exception:  # noqa: BLE001 - a placement that cannot say is written out
        pass
    return {
        "place": {
            "origin": vec(place.origin),
            "xdir": vec(place.xdir, DIR_NDIGITS),
            "zdir": vec(place.zdir, DIR_NDIGITS),
        }
    }


def _poly_frame(poly) -> dict:
    return {
        "origin": vec(poly.origin),
        "normal": vec(poly.normal, DIR_NDIGITS),
        "xdir": vec(poly.xdir, DIR_NDIGITS),
    }


def primitive_to_dict(prim, ndigits: int = NDIGITS) -> dict:
    """One boolean's primitive as a dict (see the module's description)."""
    from ada import (
        BoolHalfSpace,
        PrimBox,
        PrimCone,
        PrimCyl,
        PrimExtrude,
        PrimRevolve,
        PrimSphere,
    )

    if isinstance(prim, PrimBox):
        out = {"kind": "box", "p1": vec(prim.p1, ndigits), "p2": vec(prim.p2, ndigits)}
    elif isinstance(prim, PrimCone):
        out = {"kind": "cone", "p1": vec(prim.p1, ndigits), "p2": vec(prim.p2, ndigits), "r": num(prim.r, ndigits)}
    elif isinstance(prim, PrimCyl):
        out = {"kind": "cyl", "p1": vec(prim.p1, ndigits), "p2": vec(prim.p2, ndigits), "r": num(prim.r, ndigits)}
    elif isinstance(prim, PrimSphere):
        out = {"kind": "sphere", "center": vec(prim.cog, ndigits), "r": num(prim.radius, ndigits)}
    elif isinstance(prim, PrimExtrude):
        out = {
            "kind": "extrude",
            "points": outline_to_list(prim.poly, ndigits),
            "h": num(prim.extrude_depth, ndigits),
            **_poly_frame(prim.poly),
        }
    elif isinstance(prim, PrimRevolve):
        out = {
            "kind": "revolve",
            "points": outline_to_list(prim.poly, ndigits),
            "angle": num(prim.revolve_angle, ndigits),
            **_poly_frame(prim.poly),
        }
    elif isinstance(prim, BoolHalfSpace):
        out = {
            "kind": "half_space",
            "origin": vec(prim.poly.origin, ndigits),
            "normal": vec(prim.poly.normal, DIR_NDIGITS),
        }
        if prim.flip:
            out["flip"] = True
        return out
    else:
        return {"kind": type(prim).__name__, "note": "not written: a primitive of this kind has no description here"}
    out.update(_place_dict(prim))
    return out


def booleans_to_list(obj, ndigits: int = NDIGITS) -> list[dict]:
    """``obj``'s booleans (a beam's cuts), each :func:`primitive_to_dict` with ``op`` where it is not a cut.
    Empty for none."""
    out = []
    for b in getattr(obj, "booleans", None) or []:
        prim = getattr(b, "primitive", b)
        try:
            item = primitive_to_dict(prim, ndigits)
        except Exception as exc:  # noqa: BLE001 - one primitive that cannot be read is said, not dropped
            item = {"kind": type(prim).__name__, "note": f"not written: {exc}"}
        op = getattr(getattr(b, "bool_op", None), "value", None)
        if _KIND_OF_OP.get(str(op)) is not None:
            item["op"] = _KIND_OF_OP[str(op)]
        out.append(item)
    return out


class UnknownPrimitive(ValueError):
    """A boolean whose primitive the dict does not describe (``note``)."""


def primitive_from_dict(d: dict, name: str):
    """The primitive :func:`primitive_to_dict` wrote. Raises :class:`UnknownPrimitive` for one it could not."""
    from ada import (
        BoolHalfSpace,
        Placement,
        PrimBox,
        PrimCone,
        PrimCyl,
        PrimExtrude,
        PrimRevolve,
        PrimSphere,
    )

    kind = d.get("kind")
    kwargs: dict[str, Any] = {}
    if d.get("place"):
        p = d["place"]
        kwargs["placement"] = Placement(origin=p["origin"], xdir=p.get("xdir"), zdir=p.get("zdir"))
    if kind == "box":
        return PrimBox(name, d["p1"], d["p2"], **kwargs)
    if kind == "cyl":
        return PrimCyl(name, d["p1"], d["p2"], float(d["r"]), **kwargs)
    if kind == "cone":
        return PrimCone(name, d["p1"], d["p2"], float(d["r"]), **kwargs)
    if kind == "sphere":
        return PrimSphere(name, d["center"], float(d["r"]), **kwargs)
    if kind == "extrude":
        pts = [tuple(float(c) for c in p) for p in d["points"]]
        return PrimExtrude(name, pts, float(d["h"]), normal=d["normal"], origin=d["origin"], xdir=d["xdir"], **kwargs)
    if kind == "revolve":
        pts = [tuple(float(c) for c in p) for p in d["points"]]
        return PrimRevolve(
            name, pts, float(d["angle"]), origin=d["origin"], xdir=d["xdir"], normal=d["normal"], **kwargs
        )
    if kind == "half_space":
        return BoolHalfSpace(d["origin"], d["normal"], flip=bool(d.get("flip")), name=name)
    raise UnknownPrimitive(f"boolean {name!r}: {kind!r} cannot be made ({d.get('note') or 'unknown kind'})")


def booleans_from_list(items: list[dict] | None, prefix: str = "bool", strict: bool = True) -> list[Boolean]:
    """The booleans :func:`booleans_to_list` wrote, as ``ada.Boolean``. A primitive that was not written
    (``note``) raises :class:`UnknownPrimitive` when ``strict``; else it is left out with a warning."""
    from ada import Boolean
    from ada.geom.booleans import BoolOpEnum

    out = []
    for i, d in enumerate(items or []):
        name = f"{prefix}_{i + 1}"
        try:
            prim = primitive_from_dict(d, name)
        except UnknownPrimitive:
            if strict:
                raise
            logger.warning(f"member_dict: {name} ({d.get('kind')}) is left out: {d.get('note')}")
            continue
        op = BoolOpEnum.from_str(d.get("op") or "difference") or BoolOpEnum.DIFFERENCE
        out.append(Boolean(prim, op))
    return out


def add_booleans(beam: Beam, items: list[dict] | None, strict: bool = True) -> Beam:
    """Re-add the booleans of a :func:`booleans_to_list` to ``beam``; the beam."""
    for b in booleans_from_list(items, prefix=f"{beam.name}_bool", strict=strict):
        beam.add_boolean(b.primitive, b.bool_op)
    return beam


# ---- a beam -------------------------------------------------------------------------------------------------------


def beam_from_metadata(meta: dict) -> Beam:
    """The beam a ``beam_metadata`` dict describes: its name, axis (``n1``, ``n2``, ``up``), section (by outline
    for a POLY), material by name, and booleans."""
    import ada

    axis = meta.get("axis") or meta
    kwargs: dict[str, Any] = {}
    if axis.get("up"):
        kwargs["up"] = tuple(axis["up"])
    mat = (meta.get("material") or {}).get("name")
    if mat:
        kwargs["mat"] = mat
    beam = ada.Beam(meta["name"], tuple(axis["n1"]), tuple(axis["n2"]), section_from_dict(meta["section"]), **kwargs)
    return add_booleans(beam, meta.get("booleans"))


__all__ = [
    "NDIGITS",
    "UnknownPrimitive",
    "add_booleans",
    "beam_from_metadata",
    "booleans_from_list",
    "booleans_to_list",
    "outline_from_list",
    "outline_to_list",
    "primitive_from_dict",
    "primitive_to_dict",
    "section_from_dict",
    "section_outline",
]
