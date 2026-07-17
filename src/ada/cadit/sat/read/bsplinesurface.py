from __future__ import annotations

from ada import Point
from ada.cadit.sat.exceptions import ACISReferenceDataError, ACISUnsupportedSurfaceType
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.cadit.sat.read.sat_utils import get_ref_type
from ada.config import logger
from ada.geom.curve_utils import calculate_multiplicities
from ada.geom.curves import KnotType
from ada.geom.surfaces import (
    BSplineSurfaceForm,
    BSplineSurfaceWithKnots,
    RationalBSplineSurfaceWithKnots,
)


def _peel_exppc_to_inner_surface(data_lines: list[str]) -> tuple[list[str] | None, str | None]:
    """Strip the ``exppc`` 2D-pcurve wrapper to expose the inner 3D surface.

    An ACIS ``exppc`` subtype is a parameter-space curve embedded in a 3D
    surface; the wrapper carries pcurve data (degree / knots / 2D control
    points) followed by either:

      * ``spline <sense> { exactsur ... }`` — the 3D surface inline, OR
      * ``spline <sense> { ref N }``        — another level of indirection
                                               into the SAT reference table.

    Returns ``(inner_data_lines, ref_id)`` — exactly one of the two is
    non-None on success:

      * ``inner_data_lines`` non-None when an inline exactsur was found;
        ready for the existing exactsur parser as data_lines[0..].
      * ``ref_id`` non-None when the inner is a ``ref N``; caller must
        resolve N via the SAT store and recurse.

    Returns ``(None, None)`` if neither pattern matches.

    Sense handling: the wrapper's ``spline reversed { ... }`` indicates
    the surface's V parameter runs opposite to the pcurve's. We don't
    re-orient here — downstream OCC face construction handles edge-vs-
    surface sense composition. Leaving as-is is the conservative choice
    until we observe a face that misrenders specifically because of it.
    """
    for i, line in enumerate(data_lines):
        s = line.strip()
        if not s.startswith("spline ") or "{" not in s:
            continue
        inner = s[s.index("{") + 1 :].strip()
        # Strip the trailing ``}`` if it sits on the same line so the
        # ``ref N`` branch below sees a clean tail.
        inner = inner.rstrip("}").strip()
        # Inline ref: ``ref N`` — caller follows the chain.
        parts = inner.split()
        if len(parts) >= 2 and parts[0] == "ref":
            return None, parts[1]
        if not inner.startswith("exactsur"):
            return None, None
        new_lines = [inner] + [l.strip() for l in data_lines[i + 1 :]]
        # Trim the closing brace + any trailing F/T flag tail that
        # follows the control-point block. The exactsur parser stops
        # after reading ctrl_pts_u * ctrl_pts_v lines so trailing
        # noise is harmless, but it's cleaner to drop it.
        for j, l in enumerate(new_lines):
            if "}" in l:
                head = l[: l.index("}")].strip()
                new_lines = new_lines[:j] + ([head] if head else [])
                break
        return (new_lines or None), None
    return None, None


def _resolve_exppc_chain(sub_type, max_steps: int = 8) -> list[str]:
    """Walk an ``exppc`` chain to its terminal inline surface block.

    Some exppc records embed the surface directly (``spline { exactsur ... }``);
    others nest 1-N levels of ``spline { ref N }`` indirection that the
    SAT reference table resolves. This walker follows refs and re-peels
    until either an inline ``exactsur`` is found or we hit something we
    can't interpret as a 3D surface.

    Notably, deeper refs sometimes terminate on ``exactcur`` records —
    those are 1D BSpline *curves* in 3D space, used together with the
    outer 2D pcurve to define a swept/ruled surface. We don't synthesise
    swept surfaces from a curve+pcurve pair today; the caller's
    failure-bucket logging surfaces these as a distinct category so we
    know what's missing.

    Returns the inner-surface ``data_lines`` ready for the existing
    exactsur parser. Raises ``ACISReferenceDataError`` (with a
    descriptive message) when the chain doesn't terminate at an
    exactsur block.
    """
    for step in range(max_steps):
        if sub_type.type == "exppc":
            data_lines = sub_type.get_as_string().splitlines()
            inner_lines, ref_id = _peel_exppc_to_inner_surface(data_lines)
            if inner_lines is not None:
                return inner_lines
            if ref_id is None:
                raise ACISReferenceDataError("exppc subtype with no inner exactsur or ref N (unknown shape)")
            try:
                sub_type = sub_type.parent_record.sat_store.get_ref(ref_id)
            except (KeyError, AttributeError) as exc:
                raise ACISReferenceDataError(f"exppc ref {ref_id} did not resolve to a SAT record") from exc
            continue
        # Reached a non-exppc record after one or more ref hops. Only
        # ``exactsur`` is parseable as a surface here; everything else
        # (notably ``exactcur`` — a 1D BSpline curve, or
        # ``lawintcur`` — a law-defined intersection curve) implies a
        # swept/ruled surface that we don't synthesise yet.
        if sub_type.type == "exactsur":
            return sub_type.get_as_string().splitlines()
        raise ACISReferenceDataError(
            f"exppc chain terminates at {sub_type.type!r}, not exactsur "
            f"(implies swept/ruled surface from base curve — not yet supported)"
        )
    raise ACISReferenceDataError(f"exppc chain did not terminate within {max_steps} hops")


def create_bsplinesurface_from_sat(
    spline_surface_record: AcisRecord,
) -> BSplineSurfaceWithKnots | RationalBSplineSurfaceWithKnots:
    sub_type = spline_surface_record.get_sub_type()

    if sub_type.type == "ref":
        original_sub_type = sub_type
        sub_type = get_ref_type(sub_type)
        if sub_type.type in ("exactcur", "lawintcur"):
            raise ACISReferenceDataError(f"Subtype is exactcur when following references from {original_sub_type}")

    if sub_type.type == "exppc":
        # Peel off the 2D pcurve wrapper to expose the inner 3D
        # surface. Some exppc records inline the surface directly
        # (``spline { exactsur ... }``); others nest 1-N levels of
        # ``spline { ref N }`` indirection — the resolver walks
        # both patterns. ``exppc`` was the largest silent-fallback
        # bucket before this (~30% of all spline-surface faces).
        data_lines = _resolve_exppc_chain(sub_type)
    else:
        data_lines = sub_type.get_as_string().splitlines()
    dline = data_lines[0].split()
    # Check for the extra "0" after "exactsur"
    has_extra_zero = dline[1] == "0"

    # Adjust indices based on whether the "0" is present
    surface_type_idx = 3 if has_extra_zero else 2
    u_degree_idx = 4 if has_extra_zero else 3
    v_degree_idx = 5 if has_extra_zero else 4

    # Surface type: "nurbs", "nubs", or "nullbs"
    surface_type = dline[surface_type_idx]

    if surface_type == "nullbs":
        raise ACISUnsupportedSurfaceType("Null B-spline surfaces not supported")

    # Degrees in U and V directions
    u_degree = int(dline[u_degree_idx])
    v_degree = int(dline[v_degree_idx])

    # Knot closure type: "open", "closed", or "periodic"
    # u_closure_idx = 7 if has_extra_zero else 5
    # v_closure_idx = 8 if has_extra_zero else 6
    # u_closure = dline[u_closure_idx]
    # v_closure = dline[v_closure_idx]

    # The trailing header pair is the number of distinct U and V knots. Each
    # (value, multiplicity) knot vector can wrap across several physical lines,
    # so accumulate tokens line by line rather than assuming U sits wholly on
    # line 1 and V on line 2 (same multi-line-knot bug fixed on the curve side
    # in bsplinecurves.py). U knots always end on a line boundary before V
    # begins, and V before the control points.
    num_u_knots = int(dline[-2])
    num_v_knots = int(dline[-1])

    line_idx = 1
    u_tokens: list[str] = []
    while len(u_tokens) < 2 * num_u_knots and line_idx < len(data_lines):
        u_tokens.extend(data_lines[line_idx].split())
        line_idx += 1
    v_tokens: list[str] = []
    while len(v_tokens) < 2 * num_v_knots and line_idx < len(data_lines):
        v_tokens.extend(data_lines[line_idx].split())
        line_idx += 1

    # Parse U knot vector
    u_knot_data = [float(x) for x in u_tokens[: 2 * num_u_knots]]
    u_knots = u_knot_data[0::2]  # U knot values
    u_multiplicities = u_knot_data[1::2]  # U knot multiplicities

    # Parse V knot vector
    v_knot_data = [float(x) for x in v_tokens[: 2 * num_v_knots]]
    v_knots = v_knot_data[0::2]  # V knot values
    v_multiplicities = v_knot_data[1::2]  # V knot multiplicities

    # Number of control points in U and V directions
    control_points_u = int(sum(u_multiplicities)) + 1 - u_degree
    control_points_v = int(sum(v_multiplicities)) + 1 - v_degree

    # Parse control points from the first line after the knot vectors
    control_point_start_line = line_idx  # Line where control points begin

    # Initialize the control points as a list of tuples (U, V) where each tuple has two control points
    control_points = []
    weights = []

    # create a empty list of control points
    for _ in range(control_points_u):
        control_points.append([None] * control_points_v)

    # create a empty list of weights
    for _ in range(control_points_u):
        weights.append([None] * control_points_v)

    is_rational = True
    for v in range(control_points_v):
        for u in range(control_points_u):
            u_point_data = [float(x) for x in data_lines[control_point_start_line].split()]
            if len(u_point_data) == 4:
                weights[u][v] = u_point_data[3]
            else:
                is_rational = False
            control_points[u][v] = Point(*u_point_data[:3])
            control_point_start_line += 1

    surf_form = BSplineSurfaceForm.UNSPECIFIED

    num_u_points = len(control_points)
    num_v_points = len(control_points[0])
    u_mult, v_mult = calculate_multiplicities(u_degree, v_degree, u_knots, v_knots, num_u_points, num_v_points)

    if dline[0] == "exactsur":
        logger.info("Exact surface")

    if is_rational:
        surface = RationalBSplineSurfaceWithKnots(
            u_degree=u_degree,
            v_degree=v_degree,
            control_points_list=control_points,
            surface_form=surf_form,
            u_closed=False,
            v_closed=False,
            self_intersect=False,
            u_multiplicities=u_mult,
            v_multiplicities=v_mult,
            u_knots=u_knots,
            v_knots=v_knots,
            knot_spec=KnotType.UNSPECIFIED,
            weights_data=weights,
        )
    else:
        surface = BSplineSurfaceWithKnots(
            u_degree=u_degree,
            v_degree=v_degree,
            control_points_list=control_points,
            surface_form=surf_form,
            u_knots=u_knots,
            v_knots=v_knots,
            u_multiplicities=u_mult,
            v_multiplicities=v_mult,
            u_closed=False,
            v_closed=False,
            self_intersect=False,
            knot_spec=KnotType.UNSPECIFIED,
        )

    return surface
