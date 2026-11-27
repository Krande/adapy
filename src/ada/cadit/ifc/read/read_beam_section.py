from ada.base.units import Units
from ada.config import logger
from ada.sections import Section
from ada.sections.categories import BaseTypes


def import_section_from_ifc(profile_def, units=Units.M) -> Section:
    """Takes any subclass of ProfileDef and returns an adapy Section.

    Resolution order:
      1. adapy parameter bag (IfcProfileProperties) -> exact parametric section.
      2. Native parametric IFC profile defs (I/T/circle-hollow).
      3. Polyline/arbitrary geometry -> recognise parametric or keep as POLY.
      4. Last resort: parse the profile name string.
    """
    from ada.cadit.ifc.sections_props import (
        read_profile_section_params,
        section_from_param_dict,
    )
    from ada.sections.string_to_section import interpret_section_str

    name = getattr(profile_def, "ProfileName", None)

    # 1. Exact round-trip via the adapy parameter bag (works for every type). A POLY section's bag
    # holds only the scalars its curves cannot state; its shape is still read from the curves.
    params = read_profile_section_params(profile_def)
    if params is not None and params.get("sec_type") == BaseTypes.POLY.value:
        sec = _section_from_arbitrary_profile(profile_def, name, units, params=params)
        if sec is not None:
            return sec
    elif params is not None:
        return section_from_param_dict(name, params, units)

    # 2. Native parametric profile defs.
    if profile_def.is_a("IfcIShapeProfileDef"):
        return Section(
            name=name,
            sec_type=Section.TYPES.IPROFILE,
            h=profile_def.OverallDepth,
            w_top=profile_def.OverallWidth,
            w_btn=profile_def.OverallWidth,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            # Flange-root fillet radius (optional in IFC). Stored in the otherwise-unused ``r``
            # for I-sections so iprofiles() rounds the four web/flange junctions instead of
            # drawing sharp corners.
            r=profile_def.FilletRadius,
            units=units,
            sec_str=name,
        )
    elif profile_def.is_a("IfcTShapeProfileDef"):
        # Adapy's TPROFILE convention (see string_to_section): the absent bottom
        # flange is encoded collapsed-onto-the-web (w_btn = t_w, t_fbtn = t_ftop),
        # not None — the section writers (e.g. Genie XML's unsymmetrical_i_section)
        # do arithmetic on these fields.
        return Section(
            name=name,
            sec_type=Section.TYPES.TPROFILE,
            h=profile_def.Depth,
            w_top=profile_def.FlangeWidth,
            w_btn=profile_def.WebThickness,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            units=units,
        )
    elif profile_def.is_a("IfcUShapeProfileDef"):
        # Mirrors ChannelProfile.get_ifc_props (write_sections): Depth=h,
        # FlangeWidth=w_top, WebThickness=t_w, FlangeThickness=t_ftop. UNP
        # channels are symmetric so both flanges share width/thickness.
        return Section(
            name=name,
            sec_type=Section.TYPES.CHANNEL,
            h=profile_def.Depth,
            w_top=profile_def.FlangeWidth,
            w_btn=profile_def.FlangeWidth,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            units=units,
        )
    elif profile_def.is_a("IfcCircleHollowProfileDef"):
        return Section(
            name=name,
            sec_type="TUB",
            r=profile_def.Radius,
            wt=profile_def.WallThickness,
            units=units,
        )

    # 3. Polyline / arbitrary geometry -> reconstruct from the curves.
    if profile_def.is_a("IfcArbitraryClosedProfileDef") or profile_def.is_a("IfcArbitraryProfileDefWithVoids"):
        sec = _section_from_arbitrary_profile(profile_def, name, units)
        if sec is not None:
            return sec

    # 4. Last resort: interpret the profile name string.
    try:
        logger.info(f'No native/geometry support for Ifc beam "{profile_def=}", trying name parse')
        sec, _ = interpret_section_str(profile_def.ProfileName)
    except Exception as e:
        logger.warning(f'Unable to process section "{name}" -> error: "{e}"')
        sec = None
    if sec is None:
        raise NotImplementedError(f'IFC section type "{profile_def}" is not yet implemented')

    return sec


def _section_from_arbitrary_profile(profile_def, name, units, params: dict | None = None) -> Section | None:
    """Reconstruct a Section from an arbitrary (polyline) profile def. ``params``: a POLY
    section's own scalars from an adapy parameter bag (``section_from_polyline``)."""
    from ada.cadit.ifc.read.geom.surfaces import arbitrary_closed_profile_def
    from ada.sections.from_geometry import section_from_polyline

    try:
        apd = arbitrary_closed_profile_def(profile_def)
        outer = _curve_to_points2d(apd.outer_curve)
        inner_curves = [_curve_to_points2d(c) for c in apd.inner_curves]
    except NotImplementedError as e:
        logger.warning(f'Unable to read arbitrary profile geometry for "{name}" -> {e}')
        return None

    inner = inner_curves[0] if inner_curves else None
    return section_from_polyline(outer, inner, name=name, units=units, params=params)


def _curve_to_points2d(curve) -> list[tuple[float, ...]]:
    from ada.geom import curves as geo_cu

    if isinstance(curve, geo_cu.PolyLine):
        raw = [tuple(p) for p in curve.points]
    elif isinstance(curve, geo_cu.IndexedPolyCurve):
        filleted = _filleted_corner_points(curve.segments)
        if filleted is not None:
            return filleted
        raw = curve.get_points()
    else:
        raise NotImplementedError(f"Unsupported profile curve geometry {type(curve).__name__}")

    return [(float(p[0]), float(p[1])) for p in raw]


def _filleted_corner_points(segments, tol: float = 1e-6) -> list[tuple[float, ...]] | None:
    """A closed outline of straight edges whose corners are rounded by tangent arcs, as the
    ``(x, y, r)`` corner points a ``CurvePoly2d`` is defined by -- the form the writer emits those
    arcs from (``CurvePoly2d.segments3d``). Each arc is the fillet of the corner where its two
    neighbouring edges meet, so the corner is their intersection and ``r`` the arc's radius.

    None when there is no arc, or any arc is not such a fillet (between two edges, tangent to
    both): that outline is not one corner points can state, and is read as points, as before.
    """
    import numpy as np

    from ada.geom import curves as geo_cu

    n = len(segments)
    if n < 3 or not any(isinstance(s, geo_cu.ArcLine) for s in segments):
        return None

    def xy(p) -> np.ndarray:
        return np.asarray(p, dtype=float)[:2]

    def corner_of(prev, arc, nxt):
        if not (isinstance(prev, geo_cu.Edge) and isinstance(nxt, geo_cu.Edge)):
            return None
        s, m, e = xy(arc.start), xy(arc.midpoint), xy(arc.end)
        # The circle through the arc's three points.
        a = np.array([[s[0] - m[0], s[1] - m[1]], [m[0] - e[0], m[1] - e[1]]]) * 2.0
        b = np.array([s @ s - m @ m, m @ m - e @ e])
        if abs(np.linalg.det(a)) < 1e-18:
            return None
        center = np.linalg.solve(a, b)
        radius = float(np.linalg.norm(s - center))
        d_in = xy(prev.end) - xy(prev.start)
        d_out = xy(nxt.end) - xy(nxt.start)
        if np.linalg.norm(d_in) < tol or np.linalg.norm(d_out) < tol:
            return None
        d_in, d_out = d_in / np.linalg.norm(d_in), d_out / np.linalg.norm(d_out)
        # Tangent at both ends, and joined to the edges it rounds.
        if abs((s - center) @ d_in) > tol * max(1.0, radius) or abs((e - center) @ d_out) > tol * max(1.0, radius):
            return None
        if np.linalg.norm(xy(prev.end) - s) > tol or np.linalg.norm(xy(nxt.start) - e) > tol:
            return None
        cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
        if abs(cross) < 1e-12:
            return None
        # Intersection of the incoming edge's line (through s) and the outgoing one's (through e).
        t = ((e - s)[0] * d_out[1] - (e - s)[1] * d_out[0]) / cross
        corner = s + t * d_in
        return float(corner[0]), float(corner[1]), radius

    points: list[tuple[float, ...]] = []
    for i, seg in enumerate(segments):
        prev, nxt = segments[i - 1], segments[(i + 1) % n]
        if isinstance(seg, geo_cu.ArcLine):
            corner = corner_of(prev, seg, nxt)
            if corner is None:
                return None
            points.append(corner)
        elif isinstance(seg, geo_cu.Edge):
            if isinstance(prev, geo_cu.ArcLine):
                continue  # its start is the arc's end: that corner is the arc's
            start = xy(seg.start)
            points.append((float(start[0]), float(start[1])))
        else:
            return None
    return points
