from __future__ import annotations

import ada.geom.curves as geo_cu
from ada.cadit.sat.exceptions import (
    ACISIncompleteCtrlPoints,
    ACISReferenceDataError,
    ACISUnsupportedCurveType,
)
from ada.cadit.sat.read.sat_entities import AcisRecord, AcisSubType
from ada.cadit.sat.read.sat_utils import get_ref_type
from ada.config import logger
from ada.geom.curves import BSplineCurveFormEnum, KnotType


def extract_data_lines(data: str) -> list[str]:
    data_lines = []
    for x in data.splitlines():
        line_data = x.strip()
        if not line_data:
            continue
        if "}" in x:
            break
        data_lines.append(line_data)
    return data_lines


def get_curve_type(dline: list[str], has_extra_zero) -> str:
    # Adjust indices based on whether the "0" is present
    curve_type_idx = 3 if has_extra_zero else 2

    # Curve type: "nurbs", "nubs", or "nullbs"
    return dline[curve_type_idx]


def get_degree_and_closure(dline: list[str], has_extra_zero) -> tuple[int, bool]:
    # Adjust indices based on whether the "0" is present
    u_degree_idx = 4 if has_extra_zero else 3
    u_closure_idx = 5 if has_extra_zero else 4

    degree = int(dline[u_degree_idx])
    closed_curve = False if dline[u_closure_idx] == "open" else True

    return degree, closed_curve


def create_bspline_curve_from_lawintcur(data_lines: list[str]) -> geo_cu.BSplineCurveWithKnots | None:
    """Create a B-spline curve from a lawintcur data string."""
    logger.debug(f"create_bspline_curve_from_lawintcur called with {len(data_lines)} lines")

    # Extract degree and closed/open status
    dline = data_lines[0].split()

    if dline[0] == "ref":
        raise ACISReferenceDataError("Reference data not supported")

    has_extra_zero = dline[1] != "full"

    spl_type = dline[0]
    logger.info(f"Creating B-spline curve of type {spl_type}")

    # Curve type: "nurbs", "nubs", or "nullbs"
    curve_type = get_curve_type(dline, has_extra_zero)
    if curve_type == "nullbs":
        raise ACISReferenceDataError("Null B-spline surfaces not supported")

    degree, closed_curve = get_degree_and_closure(dline, has_extra_zero)

    # Extract knots and multiplicities
    knots_in = [float(x) for x in data_lines[1].split()]
    ctrl_point_line_idx = 2
    if len(data_lines[2].split()) != 3:
        knots_in += [float(x) for x in data_lines[2].split()]
        ctrl_point_line_idx = 3

    knots = knots_in[0::2]
    mult = [int(x) for x in knots_in[1::2]]

    # Adjust knot multiplicities to satisfy IFC requirements
    mult[0] = degree + 1  # Start multiplicity
    mult[-1] = degree + 1  # End multiplicity
    total_knots = sum(mult)
    num_control_points = total_knots - degree - 1

    # Extract control points
    control_point_lines = data_lines[ctrl_point_line_idx : +ctrl_point_line_idx + num_control_points]
    control_points = []
    for line in control_point_lines:
        if line.strip() == "0":  # End of control points
            break
        lsplit = line.split()
        if len(lsplit) < 3:
            raise ACISIncompleteCtrlPoints("Incomplete control point data: {}".format(line))

        values = [float(i) for i in lsplit]
        control_points.append(values)

    if len(control_points) != num_control_points:
        raise ACISIncompleteCtrlPoints(
            "Mismatch in number of control points. Expected {}, got {}.".format(num_control_points, len(control_points))
        )

    # Extract weights if present
    weights = None
    if len(control_points[0]) == 4:
        weights = [cp[-1] for cp in control_points]
        control_points = [cp[:3] for cp in control_points]

    # Create the B-spline curve
    if weights:
        curve = geo_cu.RationalBSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
            weights_data=weights,
        )
    else:
        curve = geo_cu.BSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
        )
    return curve


def create_bspline_curve_from_exactcur(data_lines: list[str]) -> geo_cu.BSplineCurveWithKnots | None:
    """Create a B-spline curve from an exact_sur data string."""
    logger.debug(f"create_bspline_curve_from_exactcur called with {len(data_lines)} lines")
    dline = data_lines[0].split()
    should_bump = dline[1] != "full"

    # Curve type: "nurbs", "nubs", or "nullbs"
    curve_type = get_curve_type(dline, should_bump)
    if curve_type == "nullbs":
        raise ACISReferenceDataError("Null B-spline surfaces not supported")

    degree, closed_curve = get_degree_and_closure(dline, should_bump)

    # The header ends with the number of distinct knots. Its (value,
    # multiplicity) pairs can wrap across several physical lines, and only
    # after all of them do the control points begin. Accumulate knot tokens
    # line by line until we have all ``num_distinct_knots`` pairs, so the
    # split is independent of where the source wraps — reading only
    # ``data_lines[1]`` mis-read a multi-line knot vector as control points.
    u_closure_idx = 5 if should_bump else 4
    num_distinct_knots = int(dline[u_closure_idx + 1])

    knot_tokens: list[str] = []
    ctrl_point_line_idx = len(data_lines)
    for i in range(1, len(data_lines)):
        knot_tokens.extend(data_lines[i].split())
        if len(knot_tokens) >= 2 * num_distinct_knots:
            ctrl_point_line_idx = i + 1
            break

    knots_in = [float(x) for x in knot_tokens[: 2 * num_distinct_knots]]
    logger.debug(f"knots_in len: {len(knots_in)}")
    knots = knots_in[0::2]
    mult = [int(x) for x in knots_in[1::2]]
    logger.debug(f"knots len: {len(knots)}, mult len: {len(mult)}")

    # Adjust knot multiplicities to satisfy IFC requirements
    mult[0] = degree + 1  # Start multiplicity
    mult[-1] = degree + 1  # End multiplicity
    total_knots = sum(mult)
    num_control_points = total_knots - degree - 1

    # Extract control points
    control_point_lines = data_lines[ctrl_point_line_idx:]
    control_points = []
    for line in control_point_lines:
        if line.strip() == "0":  # End of control points
            break
        lsplit = line.split()
        if len(lsplit) < 3:
            logger.warning("Incomplete control point data: {}".format(line))
            break
        values = [float(i) for i in lsplit]

        control_points.append(values)

    if len(control_points) != num_control_points:
        logger.error(
            "Mismatch in number of control points. Expected {}, got {}.".format(num_control_points, len(control_points))
        )
        return None

    # Extract weights if present
    weights = None
    if len(control_points[0]) == 4:
        weights = [cp[-1] for cp in control_points]
        control_points = [cp[:3] for cp in control_points]

    # Create the B-spline curve
    if weights:
        curve = geo_cu.RationalBSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
            weights_data=weights,
        )
    else:
        curve = geo_cu.BSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
        )
    return curve


def _peel_exppc_curve_to_inner(data_lines: list[str]) -> tuple[list[str] | None, str | None]:
    """Same idea as the surface-side peel, but for curves. ``exppc`` curve
    records carry 2D pcurve data on lines 0-3, then a wrapped 3D space
    curve on a later line as either:

      * ``spline <sense> { exactcur ... }``  / ``{ lawintcur ... }`` inline,
      * ``spline <sense> { ref N }`` — another level of indirection.

    Returns ``(inner_data_lines, ref_id)`` — exactly one is non-None on
    success. The inner data starts at the inline curve type token
    (``exactcur`` / ``lawintcur``) so the existing
    ``create_bspline_curve_from_exactcur`` / ``…_lawintcur`` parsers
    can consume it directly.
    """
    for i, line in enumerate(data_lines):
        s = line.strip()
        if not s.startswith("spline ") or "{" not in s:
            continue
        inner = s[s.index("{") + 1 :].strip()
        inner = inner.rstrip("}").strip()
        parts = inner.split()
        if len(parts) >= 2 and parts[0] == "ref":
            return None, parts[1]
        # Curves: only exactcur / lawintcur are downstream-parseable.
        if not (inner.startswith("exactcur") or inner.startswith("lawintcur")):
            return None, None
        new_lines = [inner] + [l.strip() for l in data_lines[i + 1 :]]
        # Trim trailing brace block.
        for j, l in enumerate(new_lines):
            if "}" in l:
                head = l[: l.index("}")].strip()
                new_lines = new_lines[:j] + ([head] if head else [])
                break
        return (new_lines or None), None
    return None, None


def _resolve_exppc_curve_chain(sub_type: AcisSubType, max_steps: int = 8) -> list[str]:
    """Walk an ``exppc`` curve chain to its terminal inline 3D curve block.

    Mirrors ``_resolve_exppc_chain`` on the surface side. Accepts inline
    ``exactcur`` / ``lawintcur``; raises ``ACISUnsupportedCurveType`` for
    chains that terminate on something else (typically a curve type we
    haven't taught the parser yet).
    """
    for step in range(max_steps):
        if sub_type.type == "exppc":
            data_lines = sub_type.get_as_string().splitlines()
            inner_lines, ref_id = _peel_exppc_curve_to_inner(data_lines)
            if inner_lines is not None:
                return inner_lines
            if ref_id is None:
                raise ACISUnsupportedCurveType("exppc curve subtype with no inner exactcur/lawintcur or ref N")
            try:
                sub_type = sub_type.parent_record.sat_store.get_ref(ref_id)
            except (KeyError, AttributeError) as exc:
                raise ACISReferenceDataError(f"exppc curve ref {ref_id} did not resolve") from exc
            continue
        if sub_type.type in ("exactcur", "lawintcur"):
            return sub_type.get_as_string().splitlines()
        raise ACISUnsupportedCurveType(
            f"exppc curve chain terminates at {sub_type.type!r}, " f"expected exactcur or lawintcur"
        )
    raise ACISUnsupportedCurveType(f"exppc curve chain did not terminate within {max_steps} hops")


def create_pcurve_from_exppc(exppc_sub_type: AcisSubType):
    """Resolve an ``exppc`` edge-curve to its underlying 3D BSpline curve.

    The ``exppc`` subtype on an ``intcurve-curve`` is a parameter-space
    wrapper around a 3D space curve (typically ``exactcur`` — exact 3D
    BSpline — or ``lawintcur`` — law-defined intersection curve). The 2D
    parameter-space data on lines 0-3 of the record is used elsewhere
    (per-coedge UV-curve attach via ``create_pcurve_2d_from_sat_record``);
    here we want the 3D space curve so the caller can build an OCC edge
    geometry from it.

    Was a stub until now: every exppc-typed edge curve raised
    ``PCurve is not yet supported`` and the face fell back to a flat
    polygon. On a large reference model that path failed for ~16% of
    spline-surface faces.
    """
    inner_lines = _resolve_exppc_curve_chain(exppc_sub_type)
    head = inner_lines[0].split()
    spl_type = head[0] if head else ""
    if spl_type == "exactcur":
        return create_bspline_curve_from_exactcur(inner_lines)
    if spl_type == "lawintcur":
        return create_bspline_curve_from_lawintcur(inner_lines)
    raise ACISUnsupportedCurveType(f"exppc curve resolved to unsupported inner type: {spl_type!r}")


def create_2d_pcurve_from_acis_pcurve(acis_pcurve) -> geo_cu.Pcurve2dBSpline | None:
    """Translate a parsed ``AcisPCurve`` into a ``Pcurve2dBSpline``.

    The exppc subtype carries the surface-space (UV) BSpline directly:
    degree, knots+multiplicities, and 2D control points. This is the
    representation downstream OCCT face-construction uses, replacing
    the lossy reproject-and-fit fallback in ``update_edges_uv_gen``.

    Returns ``None`` when the underlying spline data is unusable (missing
    knots / control points, or degenerate dimensions). Caller falls back
    to reprojection in that case.
    """
    sd = getattr(acis_pcurve, "spline_data", None)
    if sd is None:
        return None
    if sd.subtype != "exppc":
        # Non-exppc pcurves (rare in practice) carry only references; we
        # don't support those today and the regenerative path handles them.
        return None
    cps = sd.control_points or []
    if len(cps) < 2 or not sd.knots:
        return None
    # ``rational`` here means "stored as nurbs"; weights live in the third
    # column of each cp row when present.
    cps_2d: list[list[float]] = []
    weights: list[float] | None = []
    for row in cps:
        if len(row) < 2:
            return None
        if len(row) >= 3:
            cps_2d.append([row[0], row[1]])
            if weights is not None:
                weights.append(float(row[2]))
        else:
            cps_2d.append([float(row[0]), float(row[1])])
            weights = None  # downgrade to non-rational the moment any row lacks a weight
    knots = list(sd.knots)
    mults = [int(round(m)) for m in (sd.knot_multiplicities or [])]
    if len(mults) != len(knots) or not mults:
        return None
    # Bump end multiplicities to ``degree + 1`` for open curves — same
    # IFC normalisation the 3D path applies in ``create_bspline_curve_from_exactcur``.
    closed = sd.closure_u.value != "open" if hasattr(sd.closure_u, "value") else False
    if not closed:
        mults[0] = max(mults[0], sd.degree + 1)
        mults[-1] = max(mults[-1], sd.degree + 1)
    expected_n_poles = sum(mults) - sd.degree - 1
    if expected_n_poles != len(cps_2d):
        # Mismatch — usually means the parser stopped too early or too
        # late. Bail to regen rather than feed a malformed curve to OCCT.
        logger.debug(
            "pcurve cp/knot mismatch: expected %d poles, got %d (degree=%d, mults=%s)",
            expected_n_poles,
            len(cps_2d),
            sd.degree,
            mults,
        )
        return None
    return geo_cu.Pcurve2dBSpline(
        degree=int(sd.degree),
        control_points_2d=cps_2d,
        knots=knots,
        knot_multiplicities=mults,
        weights=weights if weights else None,
        closed=closed,
    )


def create_pcurve_2d_from_sat_record(pcurve_record: AcisRecord) -> geo_cu.Pcurve2dBSpline | None:
    """Parse an ACIS ``pcurve`` record into a 2D BSpline curve in surface UV.

    The exppc subtype carries the surface-space curve directly:
    degree, (knot, multiplicity) pairs, then 2D control points. Using
    this authored UV curve avoids the lossy 3D→reprojection→fit path
    that crashes on near-singular surface points (the source of the
    audit-29 ``double free`` heap corruption).

    Returns None for unsupported subtypes or malformed data so callers
    can fall back to reprojection cleanly.
    """
    try:
        sub_type = pcurve_record.get_sub_type()
    except Exception as ex:
        logger.debug("pcurve record had no parseable sub-type block: %s", ex)
        return None
    if sub_type is None:
        return None
    if getattr(sub_type, "type", None) == "ref":
        try:
            sub_type = get_ref_type(sub_type)
        except Exception:
            return None

    try:
        data_lines = extract_data_lines(sub_type.get_as_string())
    except Exception:
        return None
    if not data_lines:
        return None

    header = data_lines[0].split()
    if not header or header[0] != "exppc":
        # Other pcurve subtypes (rare in practice) carry only references
        # to the parent intcurve's pcurve; we don't reconstruct those.
        return None

    # Header: exppc [opt_int] nubs|nurbs <degree> [open|periodic|closed] <n_knot_pairs>
    type_idx = -1
    for i, t in enumerate(header):
        if t in ("nubs", "nurbs"):
            type_idx = i
            break
    if type_idx == -1:
        return None
    rational = header[type_idx] == "nurbs"
    try:
        degree = int(header[type_idx + 1])
    except (IndexError, ValueError):
        return None
    closure_token = None
    n_knot_pairs = None
    for i in range(type_idx + 2, len(header)):
        if header[i] in ("open", "periodic", "closed"):
            closure_token = header[i]
            try:
                n_knot_pairs = int(header[i + 1])
            except (IndexError, ValueError):
                n_knot_pairs = None
            break
    closed = closure_token != "open" if closure_token else False

    # Knots line(s) — flatten to a single list of floats then split
    # into (knot, multiplicity) pairs. Stop once we have the expected
    # number; remainder is control points.
    nums: list[float] = []
    knot_end_idx = 1
    for i in range(1, len(data_lines)):
        if n_knot_pairs is not None and len(nums) >= 2 * n_knot_pairs:
            knot_end_idx = i
            break
        toks = data_lines[i].split()
        try:
            row = [float(t) for t in toks]
        except ValueError:
            knot_end_idx = i
            break
        # An exppc 2D control point row is 2 (nubs) or 3 (nurbs+weight)
        # floats; bail before consuming it as knots when we don't know
        # the expected count.
        if n_knot_pairs is None and len(row) in (2, 3):
            knot_end_idx = i
            break
        nums.extend(row)
        knot_end_idx = i + 1
    if not nums:
        return None
    knots: list[float] = []
    mults: list[int] = []
    for j in range(0, len(nums) - 1, 2):
        knots.append(nums[j])
        mults.append(int(round(nums[j + 1])))
    if not mults:
        return None
    # IFC normalisation: bump end multiplicities to ``degree + 1`` for
    # open curves so OCCT accepts them.
    if not closed:
        mults[0] = max(mults[0], degree + 1)
        mults[-1] = max(mults[-1], degree + 1)
    expected_n_poles = sum(mults) - degree - 1
    if expected_n_poles <= 0:
        return None

    # 2D control points — collect 2-or-3-float rows up to expected count.
    cps: list[list[float]] = []
    weights: list[float] | None = []
    after_cps = len(data_lines)
    for i in range(knot_end_idx, len(data_lines)):
        if len(cps) >= expected_n_poles:
            after_cps = i
            break
        toks = data_lines[i].split()
        try:
            row = [float(t) for t in toks]
        except ValueError:
            after_cps = i
            break
        if len(row) == 2:
            cps.append(row)
            weights = None
        elif len(row) == 3:
            cps.append([row[0], row[1]])
            if weights is not None:
                weights.append(row[2])
        else:
            after_cps = i
            break
    if len(cps) != expected_n_poles:
        logger.debug(
            "pcurve cp/knot mismatch: expected %d poles, got %d (degree=%d, mults=%s)",
            expected_n_poles,
            len(cps),
            degree,
            mults,
        )
        return None

    # The lone real after the control points is the fit tolerance — how far the
    # pcurve may sit from the true curve-on-surface (SAT v4.0 ch.5). Keep it:
    # dropping it and writing 0 back out would assert an exactness the author
    # never claimed. Genie writes 0.001 on roughly a quarter of its pcurves.
    fit_tolerance = 0.0
    if after_cps < len(data_lines):
        toks = data_lines[after_cps].split()
        if len(toks) == 1:
            try:
                fit_tolerance = float(toks[0])
            except ValueError:
                pass

    return geo_cu.Pcurve2dBSpline(
        degree=degree,
        control_points_2d=cps,
        knots=knots,
        knot_multiplicities=mults,
        weights=weights if (rational and weights) else None,
        closed=closed,
        fit_tolerance=fit_tolerance,
    )


def create_bspline_curve_from_sat(spline_record: AcisRecord) -> geo_cu.BSplineCurveWithKnots | geo_cu.PCurve | None:
    sub_type = spline_record.get_sub_type()

    if sub_type.type == "ref":
        sub_type = get_ref_type(sub_type)

    data_lines = extract_data_lines(sub_type.get_as_string())
    dline = data_lines[0].split()

    spl_type = dline[0]
    if spl_type == "lawintcur":
        return create_bspline_curve_from_lawintcur(data_lines)
    elif spl_type == "exactcur":
        return create_bspline_curve_from_exactcur(data_lines)
    elif spl_type == "parcur":
        # A parameter-space curve. SESAM exports embed the 3D space curve
        # directly — ``parcur full nurbs <deg> open …`` then knots, then 3D
        # rational control points, then a ``0`` and the surface block the
        # exactcur parser stops before — so the layout past the type token is
        # identical to exactcur. Reuse it rather than evaluating the 2D UV
        # curve on the surface. Without this, curved hull-skin plates fall back
        # to a flat polygon (rendered flat instead of curved).
        return create_bspline_curve_from_exactcur(data_lines)
    elif spl_type == "surfintcur":
        # A surface-surface intersection curve. Like ``exactcur``/``parcur`` it
        # leads with its 3D approximating B-spline — ``surfintcur full nubs
        # <deg> open <n>`` then knots then 3D control points then the fit
        # tolerance — and only then the two intersecting surfaces (which the
        # exactcur parser stops before). The approximation carries a near-zero
        # fit tolerance (Genie writes 1e-11), so reusing it reproduces the edge
        # rather than re-intersecting the surfaces. Without this every plate
        # bounded by such an edge falls back to a flat polygon — on a large
        # substructure export that was ~17% of the curved faces.
        return create_bspline_curve_from_exactcur(data_lines)
    elif spl_type == "exppc":
        return create_pcurve_from_exppc(sub_type)
    else:
        raise ACISUnsupportedCurveType(f"Unsupported spline type: {spl_type}")


def _consume_bs_block(toks: list[str], i: int, dim: int):
    """Consume a ``nubs``/``nurbs``/``nullbs`` B-spline block from a token stream.

    Returns ``(parsed | None, next_index)``. Control-point count follows the ACIS
    convention: end knot multiplicities are implicitly ``degree + 1`` whatever the
    stored values say (the same clamp ``create_bspline_curve_from_exactcur``
    applies), so ``n_cp = sum(clamped mults) - degree - 1``.
    """
    kind = toks[i]
    if kind == "nullbs":
        return None, i + 1
    if kind not in ("nubs", "nurbs"):
        raise ACISUnsupportedCurveType(f"unexpected b-spline block head {kind!r}")
    rational = kind == "nurbs"
    degree = int(toks[i + 1])
    closure = toks[i + 2]
    n_knots = int(toks[i + 3])
    i += 4
    knots: list[float] = []
    mults: list[int] = []
    for _ in range(n_knots):
        knots.append(float(toks[i]))
        mults.append(int(toks[i + 1]))
        i += 2
    mults[0] = degree + 1
    mults[-1] = degree + 1
    n_cp = sum(mults) - degree - 1
    width = dim + (1 if rational else 0)
    cps: list[list[float]] = []
    weights: list[float] | None = [] if rational else None
    for _ in range(n_cp):
        vals = [float(x) for x in toks[i : i + width]]
        i += width
        cps.append(vals[:dim])
        if rational:
            weights.append(vals[-1])
    parsed = dict(
        degree=degree,
        closed=closure in ("closed", "periodic"),
        knots=knots,
        mults=mults,
        cps=cps,
        weights=weights,
    )
    return parsed, i


def _consume_range4(toks: list[str], i: int) -> int:
    """Step over a surface's four u/v range bounds: each is ``I`` (infinite, one
    token) or ``F <value>`` (finite, two tokens)."""
    for _ in range(4):
        if i < len(toks) and toks[i] == "F":
            i += 2
        else:
            i += 1
    return i


def _consume_surface_block(toks: list[str], i: int) -> int:
    """Step over one surface inside a ``surfintcur`` (plane inline, spline by
    brace block, or ``nullbs``) and return the next token index."""
    kind = toks[i]
    if kind == "nullbs":
        return i + 1
    if kind == "plane":
        i += 1 + 9  # keyword + origin(3) + normal(3) + u-deriv(3)
        if i < len(toks) and toks[i] in ("forward_v", "reverse_v"):
            i += 1
        return _consume_range4(toks, i)
    if kind == "spline":
        i += 1
        if toks[i] in ("forward", "reversed"):
            i += 1
        if toks[i] != "{":
            raise ACISUnsupportedCurveType("surfintcur spline surface without brace block")
        depth = 0
        while i < len(toks):
            if toks[i] == "{":
                depth += 1
            elif toks[i] == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        return _consume_range4(toks, i)
    raise ACISUnsupportedCurveType(f"unsupported surface kind in surfintcur: {kind!r}")


def _pcurve_from_bs(parsed, fit_tolerance: float) -> geo_cu.Pcurve2dBSpline | None:
    if parsed is None:
        return None
    return geo_cu.Pcurve2dBSpline(
        degree=parsed["degree"],
        control_points_2d=[tuple(cp) for cp in parsed["cps"]],
        knots=parsed["knots"],
        knot_multiplicities=parsed["mults"],
        weights=parsed["weights"],
        closed=parsed["closed"],
        fit_tolerance=fit_tolerance,
        same_sense=True,  # the caller applies the pcurve record's ±index sign
    )


def create_surface_curve_from_sat(spline_record: AcisRecord) -> geo_cu.SurfaceCurve | None:
    """A ``surfintcur`` intcurve as a :class:`~ada.geom.curves.SurfaceCurve`.

    Parses the *whole* subtype — the 3D B-spline plus the per-surface 2D pcurves —
    so the curve-on-surface stays analytical. Slot ``i`` of ``associated_pcurves``
    is ACIS pcurve index ``i + 1``, which is what a reference-form coedge ``pcurve``
    record (``±n $intcurve``) selects. Returns ``None`` for non-``surfintcur``
    subtypes (callers fall back to the plain 3D-curve read).
    """
    # Extract the record's outer brace block with balanced-brace walking:
    # ``get_sub_type_str`` truncates at the FIRST ``}``, which cuts an inline
    # ``surfintcur`` short at its nested ``{ ref N }`` surface and loses the
    # pcurve blocks that follow.
    toks_all = spline_record.get_as_string().split()
    try:
        start = toks_all.index("{")
    except ValueError:
        return None
    depth = 0
    end = None
    for j in range(start, len(toks_all)):
        if toks_all[j] == "{":
            depth += 1
        elif toks_all[j] == "}":
            depth -= 1
            if depth == 0:
                end = j
                break
    if end is None:
        return None
    toks = toks_all[start + 1 : end]
    if not toks:
        return None
    if toks[0] == "ref":
        # a positional back-reference; the ref table already stores the balanced
        # content of the definition it points at.
        sub_type = get_ref_type(spline_record.get_sub_type())
        toks = sub_type.chunks
    if toks[0] != "surfintcur":
        return None
    i = 1
    if toks[i] not in ("nubs", "nurbs", "nullbs"):
        i += 1  # the optional 'full' interpolation token
    curve3d_parsed, i = _consume_bs_block(toks, i, dim=3)
    if curve3d_parsed is None:
        return None
    fit_tol = float(toks[i])
    i += 1
    i = _consume_surface_block(toks, i)  # surface 1
    i = _consume_surface_block(toks, i)  # surface 2
    pc1_parsed, i = _consume_bs_block(toks, i, dim=2)
    pc2_parsed, i = _consume_bs_block(toks, i, dim=2)

    weights = curve3d_parsed["weights"]
    common = dict(
        degree=curve3d_parsed["degree"],
        control_points_list=curve3d_parsed["cps"],
        curve_form=BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=curve3d_parsed["closed"],
        self_intersect=False,
        knots=curve3d_parsed["knots"],
        knot_multiplicities=curve3d_parsed["mults"],
        knot_spec=KnotType.UNSPECIFIED,
    )
    if weights:
        curve3d = geo_cu.RationalBSplineCurveWithKnots(**common, weights_data=weights)
    else:
        curve3d = geo_cu.BSplineCurveWithKnots(**common)

    return geo_cu.SurfaceCurve(
        curve_3d=curve3d,
        associated_pcurves=[_pcurve_from_bs(pc1_parsed, fit_tol), _pcurve_from_bs(pc2_parsed, fit_tol)],
    )
