"""
Reusable validation utilities for IfcFixedReferenceSweptAreaSolid.

This module provides functions to validate key informal propositions and
schema-related conditions for IfcFixedReferenceSweptAreaSolid, including:

- SweptArea lies in z=0 plane (profile space is 2D)
- Directrix is tangent-continuous
- FixedReference is not parallel to the tangent (at least at the start), and
  where possible, not parallel anywhere along the curve (based on curve type)

Usage example:
    import ifcopenshell
    from sweep_validation import validate_fixed_reference_swept_area_solid

    f = ifcopenshell.open("path\\to\\model.ifc")
    solids = f.by_type("IfcFixedReferenceSweptAreaSolid")
    for s in solids:
        report = validate_fixed_reference_swept_area_solid(s, file=f, verbose=True)
        # report is a dict with pass/warn/fail flags and extra info

Notes:
- This module does not numerically evaluate tangents everywhere along arbitrary
  curves. Instead, it uses robust checks at the start and curve-type-specific
  sufficient criteria (e.g., for circles and lines) to guarantee non-parallelism.
- It accepts either an entity instance or an entity id (int) if `file` is provided.
"""

from __future__ import annotations

from math import sqrt
from typing import Dict, Optional, Tuple, Any

try:
    import ifcopenshell
except Exception:  # pragma: no cover - validator can still be imported for type hints
    ifcopenshell = None  # type: ignore


# ----------------------
# Basic vector math helpers
# ----------------------


def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    x, y, z = v
    n = sqrt(x * x + y * y + z * z)
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (x / n, y / n, z / n)


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _get_dir3_tuple(dir_ent) -> Tuple[float, float, float]:
    # IfcDirection#DirectionRatios is a list of floats
    vals = list(map(float, dir_ent.DirectionRatios))
    if len(vals) == 2:
        return (vals[0], vals[1], 0.0)
    return (vals[0], vals[1], vals[2])


def _axis3_from_position(pos) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Return (axis3, refdir) as 3D unit vectors from an IfcAxis2Placement3D."""
    axis3 = (0.0, 0.0, 1.0)
    refdir = (1.0, 0.0, 0.0)
    try:
        if pos.Axis is not None:
            axis3 = _normalize(_get_dir3_tuple(pos.Axis))
        if pos.RefDirection is not None:
            refdir = _normalize(_get_dir3_tuple(pos.RefDirection))
    except Exception:
        pass
    return axis3, refdir


def _get_profile_outer_points_2d(profile) -> Optional[list]:
    try:
        if profile.is_a("IfcArbitraryClosedProfileDef"):
            return [tuple(map(float, p.Coordinates)) for p in profile.OuterCurve.Points]
    except Exception:
        return None
    return None


# ----------------------
# Validation checks
# ----------------------


def validate_profile_z0(profile) -> Dict[str, Any]:
    """Check that SweptArea lies in z=0 plane (i.e., 2D profile space).

    PASS if profile has 2D points (e.g., IfcArbitraryClosedProfileDef with 2D points).
    """
    result = {"ok": False, "detail": "", "closed": None}
    if profile is None:
        result["detail"] = "No SweptArea provided"
        return result

    try:
        ok_type = profile.is_a("IfcProfileDef")
    except Exception:
        ok_type = False

    pts2d = _get_profile_outer_points_2d(profile)
    z0_ok = pts2d is not None and all(len(p) == 2 for p in pts2d)

    closed_ok = None
    if pts2d:
        closed_ok = len(pts2d) >= 2 and pts2d[0] == pts2d[-1]

    result.update(
        {
            "ok": bool(ok_type and z0_ok),
            "z0": bool(z0_ok),
            "is_profiledef": bool(ok_type),
            "closed": closed_ok,
            "detail": (
                "SweptArea is IfcProfileDef with 2D coordinates (implicit z=0)"
                if ok_type and z0_ok
                else "SweptArea not 2D or not an IfcProfileDef"
            ),
        }
    )
    return result


def validate_directrix_t_continuous(directrix) -> Dict[str, Any]:
    """Check that the Directrix is tangent-continuous.

    Heuristic:
    - If IfcCompositeCurve: verify every segment Transition == CONTINUOUS
    - If IfcPolyline: warn (polyline is G0 by default); cannot ensure G1
    - If single IfcTrimmedCurve over a smooth basis curve: treat as continuous
    """
    res = {"ok": False, "detail": "", "type": None}
    if directrix is None:
        res["detail"] = "No Directrix"
        return res

    try:
        dtype = directrix.is_a()
    except Exception:
        dtype = None
    res["type"] = dtype

    try:
        if dtype == "IfcCompositeCurve":
            segs = list(directrix.Segments or [])
            transitions = [str(getattr(s, "Transition", "")).upper() for s in segs]
            ok = all(tr == "CONTINUOUS" for tr in transitions)
            res.update({"ok": ok, "detail": f"CompositeCurve transitions: {transitions}"})
            return res
        elif dtype == "IfcTrimmedCurve":
            # Assume basis curve continuity governs; most basic curves are C-infinity
            res.update({"ok": True, "detail": "Single trimmed curve assumed smooth"})
            return res
        elif dtype == "IfcPolyline":
            res.update(
                {"ok": False, "detail": "Polyline is only G0 continuous; tangent continuity not guaranteed (WARN)"}
            )
            return res
        else:
            res.update({"ok": False, "detail": f"Unsupported/direct type for continuity check: {dtype}"})
            return res
    except Exception as e:
        res.update({"ok": False, "detail": f"Error checking continuity: {e}"})
        return res


def validate_fixed_reference(solid) -> Dict[str, Any]:
    """Validate FixedReference against schema guidance.

    Checks:
    - Start tangent t from Position.Axis (Axis3) is approximately orthogonal to Axis1
      projection if available (informational)
    - |dot(FixedReference, t)| < 0.95 at start -> not parallel at start
    - If Directrix is circular (BasisCurve IfcCircle), PASS_ANY if FixedReference is
      parallel to circle normal (guaranteed not parallel to tangents anywhere)
    - If Directrix is a straight line, PASS_ANY if FixedReference is perpendicular to the line
    """
    res: Dict[str, Any] = {
        "ok_start": False,
        "ok_any": None,  # None=unknown, True/False if can guarantee for all points
        "detail": "",
    }

    try:
        fixed_ref = solid.FixedReference
        fixed = _normalize(_get_dir3_tuple(fixed_ref)) if fixed_ref else None
    except Exception:
        fixed = None

    if fixed is None:
        res["detail"] = "No FixedReference direction"
        return res

    # Start tangent from Position.Axis
    try:
        pos = solid.Position
        axis3, axis1 = _axis3_from_position(pos)  # axis3 ~ tangent at start per IFC definition
    except Exception:
        axis3, axis1 = (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)

    dot_ft = abs(_dot(fixed, axis3))
    res["ok_start"] = bool(dot_ft < 0.95)

    # Attempt to classify directrix basis for stronger guarantees
    any_ok: Optional[bool] = None
    detail_bits = [f"|dot(FixedReference, start_tangent)|={dot_ft:.6f}"]

    directrix = getattr(solid, "Directrix", None)
    try:
        dtype = directrix.is_a() if directrix else None
    except Exception:
        dtype = None

    try:
        if dtype == "IfcCompositeCurve":
            segs = list(directrix.Segments or [])
            basis_curves = [getattr(s.ParentCurve, "BasisCurve", None) for s in segs]
            # Check if all segments are trimmed curves of the same circle
            circles = [bc for bc in basis_curves if getattr(bc, "is_a", lambda: None)() == "IfcCircle"]
            lines = [bc for bc in basis_curves if getattr(bc, "is_a", lambda: None)() == "IfcLine"]
            if len(circles) == len(basis_curves) and len(basis_curves) > 0:
                # Use first circle's normal
                circ = circles[0]
                axis = getattr(getattr(circ, "Position", None), "Axis", None)
                if axis is not None:
                    normal = _normalize(_get_dir3_tuple(axis))
                    dot_fn = abs(_dot(fixed, normal))
                    any_ok = bool(dot_fn > 0.95)  # parallel to normal ensures orthogonal to all tangents
                    detail_bits.append(f"circle_normal_alignment={dot_fn:.6f}")
            elif len(lines) == len(basis_curves) and len(basis_curves) > 0:
                # Use first line's direction
                line = lines[0]
                ldir = _normalize(_get_dir3_tuple(getattr(line, "Dir", None)))
                dot_fl = abs(_dot(fixed, ldir))
                any_ok = bool(dot_fl < 1e-6)  # perpendicular to line direction
                detail_bits.append(f"line_perp_check={dot_fl:.6e}")
            else:
                any_ok = None
        elif dtype == "IfcTrimmedCurve":
            bc = getattr(directrix, "BasisCurve", None)
            if getattr(bc, "is_a", lambda: None)() == "IfcCircle":
                axis = getattr(getattr(bc, "Position", None), "Axis", None)
                if axis is not None:
                    normal = _normalize(_get_dir3_tuple(axis))
                    dot_fn = abs(_dot(fixed, normal))
                    any_ok = bool(dot_fn > 0.95)
                    detail_bits.append(f"circle_normal_alignment={dot_fn:.6f}")
            elif getattr(bc, "is_a", lambda: None)() == "IfcLine":
                ldir = _normalize(_get_dir3_tuple(getattr(bc, "Dir", None)))
                dot_fl = abs(_dot(fixed, ldir))
                any_ok = bool(dot_fl < 1e-6)
                detail_bits.append(f"line_perp_check={dot_fl:.6e}")
            else:
                any_ok = None
        elif dtype == "IfcPolyline":
            any_ok = None  # cannot guarantee along corners
        else:
            any_ok = None
    except Exception as e:
        detail_bits.append(f"basis_eval_error={e}")
        any_ok = None

    res["ok_any"] = any_ok
    tail = "; ".join(detail_bits)
    res["detail"] = tail
    return res


def validate_fixed_reference_swept_area_solid(solid_or_id, file=None, verbose: bool = True) -> Dict[str, Any]:
    """Validate an IfcFixedReferenceSweptAreaSolid entity.

    Parameters:
        solid_or_id: The entity instance or its STEP id (int). If an id is
            provided, `file` must be the ifcopenshell.file to resolve it.
        file: ifcopenshell.file instance, optional (required when solid_or_id is int)
        verbose: If True, prints a human-readable summary.

    Returns:
        A dict with keys: profile, directrix, fixedref, summary, and overall flags.
    """
    if ifcopenshell is None:
        raise RuntimeError("ifcopenshell is required for validation")

    solid = solid_or_id
    if isinstance(solid_or_id, int):
        if file is None:
            raise ValueError("When passing an entity id, 'file' must be provided")
        solid = file.by_id(solid_or_id)

    if solid is None or not getattr(solid, "is_a", lambda: None)() in (
        "IfcFixedReferenceSweptAreaSolid",
        "IfcDirectrixDerivedReferenceSweptAreaSolid",
    ):
        raise ValueError(f"Expected an IfcFixedReferenceSweptAreaSolid entity, not {solid.is_a() or 'unknown'}")

    profile = getattr(solid, "SweptArea", None)
    directrix = getattr(solid, "Directrix", None)

    chk_profile = validate_profile_z0(profile)
    chk_directrix = validate_directrix_t_continuous(directrix)
    chk_fixedref = validate_fixed_reference(solid)

    overall_ok = bool(
        chk_profile.get("ok")
        and (chk_directrix.get("ok") or chk_directrix.get("type") == "IfcTrimmedCurve")
        and chk_fixedref.get("ok_start")
    )

    report: Dict[str, Any] = {
        "overall_ok": overall_ok,
        "profile": chk_profile,
        "directrix": chk_directrix,
        "fixedref": chk_fixedref,
    }

    if verbose:
        print("=== Validation: IfcFixedReferenceSweptAreaSolid ===")
        # Profile
        print(
            f"[0] SweptArea on z=0 (2D profile space) & type: {'PASS' if chk_profile.get('ok') else 'FAIL'} | details: {chk_profile.get('detail')}"
        )
        closed = chk_profile.get("closed")
        if closed is not None:
            print(f"    Closed profile: {'PASS' if closed else 'FAIL'}")
        # Directrix
        dtyp = chk_directrix.get("type")
        print(
            f"[1] Directrix tangent-continuous: {'PASS' if chk_directrix.get('ok') else 'WARN' if dtyp == 'IfcPolyline' else 'FAIL'} | type={dtyp} | {chk_directrix.get('detail')}"
        )
        # FixedReference
        print(
            f"[2] FixedReference not parallel to start tangent: {'PASS' if chk_fixedref.get('ok_start') else 'FAIL'} | {chk_fixedref.get('detail')}"
        )
        any_ok = chk_fixedref.get("ok_any")
        if any_ok is True:
            print("    Non-parallel along entire directrix: PASS (by curve-type criterion)")
        elif any_ok is False:
            print("    Non-parallel along entire directrix: FAIL")
        else:
            print("    Non-parallel along entire directrix: UNKNOWN (insufficient curve info)")
        print(f"Overall: {'PASS' if overall_ok else 'CHECK WARNINGS/FAILS'}")

    return report
