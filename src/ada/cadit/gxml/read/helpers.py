from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.plates import PlateCurved
from ada.cadit.gxml.read.read_beams import el_to_beam
from ada.cadit.gxml.read.read_materials import get_materials
from ada.cadit.gxml.read.read_sections import get_sections
from ada.config import Config, logger
from ada.core.vector_utils import (
    is_coplanar_points,
    merge_coplanar_loops_by_edge_cancellation,
)
from ada.geom import Geometry

if TYPE_CHECKING:
    from ada import Part


def iter_beams_from_xml(xml_path):
    from ada import Part

    xml_root = ET.parse(str(xml_path)).getroot()
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    p = Part("tmp")
    p._sections = get_sections(xml_root, p)
    p._materials = get_materials(xml_root, p)
    for bm_el in all_beams:
        yield from el_to_beam(bm_el, p)


def apply_mass_density_factors(root, p: Part):
    mass_density_factors = {e.attrib["name"]: float(e.attrib["factor"]) for e in root.findall(".//mass_density_factor")}
    for bm in p.beams:
        mdf = bm.metadata.get("mass_density_factor_ref", None)
        if mdf is None:
            continue

        mdf_value = mass_density_factors[mdf]
        mat_name = f"{bm.material.name}_{mdf}"
        existing_mat = p.materials.name_map.get(mat_name, None)

        if existing_mat is None:
            bm.material = bm.material.copy_to(new_name=mat_name)
            bm.material.model.rho *= mdf_value
            p.add_material(bm.material)
        else:
            bm.material = existing_mat


def _collect_sat_face_point_sets(face_refs, sat_ref_d):
    """Return per-face point loops, or None if any face is unavailable or non-point-loop."""
    face_point_sets = []
    for face_ref in face_refs:
        sat_data = sat_ref_d.get(face_ref, None)
        if sat_data is None or isinstance(sat_data, Geometry):
            return None
        if not isinstance(sat_data, (list, tuple)) or len(sat_data) < 3:
            return None
        face_point_sets.append(list(sat_data))
    return face_point_sets


def _read_inline_polygon(poly_elem):
    pts = [(float(p.attrib["x"]), float(p.attrib["y"]), float(p.attrib["z"])) for p in poly_elem.findall("./position")]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _project_to_best_fit_plane(pts):
    """Project 3D wire corners onto their SVD best-fit plane.

    The default ``Plate.from_3d_points`` derives the plate normal from
    the first three input points (a single cross product). For a curved
    SAT face whose 4-corner wire isn't coplanar, that picks an arbitrary
    plane through 3 of the 4 corners — the 4th corner ends up
    significantly off-plane and the rendered plate appears rotated
    against the rest of the model. SVD of the centred point cloud picks
    the plane that minimises perpendicular distances, then projecting
    all corners onto it gives a coplanar set of points whose orientation
    matches the curved shape's average tangent plane. Geometrically
    still a flat approximation of the curved face but no longer "tilted
    wrong" — the failure mode the user reports for the swept-surface
    fallbacks (exppc → exactcur / parcur / unresolvable ref chains).
    """
    plane = _fit_best_fit_plane(pts)
    if plane is None:
        return list(pts)
    return _project_onto_plane(pts, plane)


def _fit_best_fit_plane(pts):
    """``(centroid, unit_normal)`` of the SVD best-fit plane of ``pts``, or ``None`` if degenerate."""
    import numpy as _np

    arr = _np.asarray([list(p)[:3] for p in pts], dtype=float)
    if arr.shape[0] < 3:
        return None
    centroid = arr.mean(axis=0)
    centred = arr - centroid
    # SVD: smallest singular value corresponds to the plane normal.
    try:
        _, _, vt = _np.linalg.svd(centred, full_matrices=False)
    except _np.linalg.LinAlgError:
        return None
    normal = vt[-1]
    n_len = float(_np.linalg.norm(normal))
    if n_len < 1e-12:
        return None
    return centroid, normal / n_len


def _project_onto_plane(pts, plane):
    import numpy as _np

    centroid, normal = plane
    arr = _np.asarray([list(p)[:3] for p in pts], dtype=float)
    # p_proj = p - dot(p - centroid, n) * n
    offsets = ((arr - centroid) @ normal)[:, None] * normal
    return [tuple(p) for p in arr - offsets]


def _project_edge_curves_onto_plane(edge_curves, plane):
    """Reproject arc ``PlateEdgeCurve`` endpoints + midpoint onto ``plane`` so they still match their
    (now projected) segment in ``CurvePoly2d``. Splines are passed through unchanged (analytic spline
    fallback is a follow-up). Returns ``None``/empty unchanged."""
    from ada.api.curves import PlateEdgeCurve

    if not edge_curves or plane is None:
        return edge_curves
    out = []
    for ec in edge_curves:
        if ec.kind == "arc" and ec.midpoint is not None:
            a, b, m = _project_onto_plane([ec.a, ec.b, ec.midpoint], plane)
            out.append(PlateEdgeCurve("arc", a=a, b=b, midpoint=m))
        else:
            out.append(ec)
    return out


def _plate_from_3d_points(name, points, t, desired_normal, **kwargs):
    """``Plate.from_3d_points``, wound to face the way the XML says it does.

    A ``flat_plate`` states its normal outright as a ``<vector>``; the
    constructor ignores it and derives one from the point order instead. The two
    disagree about half the time — the points come off the SAT face's loop,
    whose winding is its own business — and the plate then goes back out facing
    the other way, which Genie draws inside-out.

    Build it, compare, and rebuild flipped when they disagree. Cheaper would be
    to reason about the winding up front, but ``CurvePoly2d`` re-orders the
    outline during construction, so its own answer is the only reliable one.
    """
    from ada import Plate

    plate = Plate.from_3d_points(name, points, t, **kwargs)
    if desired_normal is None:
        return plate
    import numpy as _np

    got = _np.asarray(plate.poly.normal, dtype=float)
    want = _np.asarray(desired_normal, dtype=float)
    if float(_np.dot(got, want)) < 0:
        plate = Plate.from_3d_points(name, points, t, flip_normal=True, **kwargs)
    return plate


def _plate_from_face(name, points, edge_curves, t, desired_normal, **kwargs):
    """Build a plate from SAT-face corner points, keeping analytic arc/spline boundary edges.

    When ``edge_curves`` (arc/spline :class:`~ada.api.curves.PlateEdgeCurve` specs) are present, the
    outline is assembled as real segments and built via ``Plate.from_segments`` so the curves survive
    analytically; otherwise it falls back to the plain point-based ``_plate_from_3d_points``. Winding is
    resolved the same way in both: build, compare to the desired normal, rebuild flipped on disagreement.
    """
    if not edge_curves:
        return _plate_from_3d_points(name, points, t, desired_normal, **kwargs)

    from ada import Plate
    from ada.api.curves import CurvePoly2d

    segments = CurvePoly2d.build_edge_segments(points, edge_curves)
    plate = Plate.from_segments(name, segments, t, **kwargs)
    if desired_normal is None:
        return plate
    import numpy as _np

    got = _np.asarray(plate.poly.normal, dtype=float)
    want = _np.asarray(desired_normal, dtype=float)
    if float(_np.dot(got, want)) < 0:
        plate = Plate.from_segments(name, segments, t, flip_normal=True, **kwargs)
    return plate


def _sense_against_face(sat_data, desired_normal, authored_sense: bool) -> bool:
    """The curved_shell sense flag: does ``desired_normal`` agree with the face?

    Only used for a plate the source stated a normal for rather than a flag (a
    ``flat_plate`` whose edges curve, so it reads as an advanced face and has to
    leave as a ``curved_shell``). The face's own normal is its surface's,
    flipped when the ACIS senses disagree — see ``get_face_same_sense``. Falls
    back to the authored flag when there is nothing to compare.
    """
    import numpy as _np

    from ada.geom import surfaces as _su

    if desired_normal is None:
        return authored_sense
    geom = getattr(sat_data, "geometry", None)
    if not isinstance(geom, _su.AdvancedFace) or not isinstance(geom.face_surface, _su.Plane):
        return authored_sense
    axis = _np.asarray(geom.face_surface.position.axis, dtype=float)
    if not geom.same_sense:
        axis = -axis
    dot = float(_np.dot(_np.asarray(desired_normal, dtype=float), axis))
    if abs(dot) < 1e-12:  # perpendicular: the comparison says nothing
        return authored_sense
    return dot > 0


def yield_plate_elems_to_plate(
    plate_elem, parent, sat_ref_d, thick_map, flat_fallback_d=None, face_normal_resolver=None, edge_curves_d=None
):
    base_name = plate_elem.attrib["name"]
    mat = parent.materials.get_by_name(plate_elem.attrib["material_ref"])
    t = thick_map.get(plate_elem.attrib.get("thickness_ref"))
    if flat_fallback_d is None:
        flat_fallback_d = {}
    if edge_curves_d is None:
        edge_curves_d = {}

    # A curved shell has no single normal to state as a vector, so Genie
    # orients it with a flag against its face's own surface normal. It is
    # authored, and mostly false (4413 of 4746 in a hull export) — defaulting
    # it to true on the way out draws the model inside-out.
    sense_flag = plate_elem.find("./local_system/sense_flag")
    sense = True
    if sense_flag is not None:
        sense = str(sense_flag.attrib.get("sense", "true")).strip().lower() != "false"
    # A flat_plate states its normal outright instead. Those still reach here as
    # PlateCurved when the face's edges curve (a flat face with a spline
    # boundary is not a polygon), and they leave as a curved_shell, which has
    # only the flag to say the same thing — so derive it rather than default it.
    desired_normal = None
    if sense_flag is None:
        vec = plate_elem.find("./local_system/vector")
        if vec is not None:
            try:
                desired_normal = tuple(float(vec.attrib[k]) for k in ("x", "y", "z"))
            except (KeyError, ValueError):
                desired_normal = None

    def normal_for_face(face_ref):
        """The normal a flat plate on this face should carry.

        A stated ``<vector>`` wins. Under a sense flag there is no vector — the
        plate's normal IS the face's, flipped when the flag says so; deriving it
        from outline winding instead sends roughly half of them out inside-out
        (the winding is the SAT loop's own business).
        """
        if desired_normal is not None or face_normal_resolver is None:
            return desired_normal
        fn = face_normal_resolver(face_ref)
        if fn is None:
            return None
        return fn if sense else tuple(-c for c in fn)

    face_elems = list(plate_elem.findall(".//face"))
    if face_elems:
        face_refs = [res.attrib["face_ref"] for res in face_elems]

        # Try to merge multi-face coplanar plates/shells into one outer loop.
        if len(face_elems) > 1:
            face_point_sets = _collect_sat_face_point_sets(face_refs, sat_ref_d)
            if face_point_sets is not None and is_coplanar_points([p for s in face_point_sets for p in s]):
                merged_points = merge_coplanar_loops_by_edge_cancellation(face_point_sets)
                if merged_points is not None:
                    try:
                        yield _plate_from_3d_points(
                            base_name,
                            merged_points,
                            t,
                            normal_for_face(face_refs[0]),
                            mat=mat,
                            metadata=dict(props=dict(gxml_face_refs=face_refs)),
                            parent=parent,
                        )
                        return
                    except Exception as e:
                        logger.error(f"Failed converting merged plate {base_name} due to {e}")
                        # fall through to per-face behavior

        for i, res in enumerate(face_elems, start=1):
            face_ref = res.attrib["face_ref"]
            name = base_name if i == 1 else f"{base_name}_{i:02d}"

            sat_data = sat_ref_d.get(face_ref, None)

            if isinstance(sat_data, Geometry) and Config().gxml_import_advanced_faces is True:
                fallback_pts = flat_fallback_d.get(face_ref)
                # World-space sanity: the recent exppc surface-peel
                # can land on an exactsur record that's the
                # parameter basis for a different geometric region
                # of the model. The resulting AdvancedFace is a
                # valid BSpline patch but tens of metres off from
                # where the wire actually lives. The peel-derived
                # surface and its wire are *both* in the wrong
                # place (consistent with each other), so a
                # surface-vs-wire check downstream wouldn't catch
                # it. The flat-plate corner points come from a
                # separate SAT path (``iter_flat_plates`` walks the
                # face's coedge endpoints), so they're an
                # independent reference for "where this plate
                # actually lives".
                #
                # If the BSpline surface centre is far from the
                # flat perimeter centroid, skip the PlateCurved
                # path entirely and yield a Plate.from_3d_points
                # at the correct world location instead.
                if (
                    fallback_pts is not None
                    and len(fallback_pts) >= 3
                    and Config().gxml_reject_deformed_curved_faces is True
                ):
                    try:
                        import numpy as _np

                        flat_arr = _np.array([list(p)[:3] for p in fallback_pts])
                        flat_min = flat_arr.min(axis=0)
                        flat_max = flat_arr.max(axis=0)
                        flat_extent = flat_max - flat_min
                        # Sample the AdvancedFace surface centre +
                        # extent from its control-points convex
                        # hull. Three failure modes the recent
                        # exppc surface-peel can hit:
                        #   * surface centroid far from the wire's
                        #     world position (peel pointed at a
                        #     different geometric region)
                        #   * surface centroid roughly OK but
                        #     parameterised over a 5-10× larger
                        #     patch (visibly stretched / deformed)
                        #   * surface centroid drifts outside the
                        #     flat bbox along *one short axis* of
                        #     a long narrow plate — the centroid-
                        #     distance check misses this because
                        #     the diagonal is dominated by the long
                        #     axis (28 m) so the tolerance is huge
                        #     even when the offset is structurally
                        #     significant in the short direction.
                        # Use AABB containment as the strict check
                        # for the third case: surface centroid must
                        # be inside the flat bbox + 25% of each
                        # axis as slack.
                        surf = getattr(sat_data.geometry, "face_surface", None)
                        cps = getattr(surf, "control_points_list", None)
                        if cps:
                            cp_arr = _np.array([[cp[0], cp[1], cp[2]] for row in cps for cp in row])
                            surf_centre = cp_arr.mean(axis=0)
                            surf_extent = cp_arr.max(axis=0) - cp_arr.min(axis=0)
                            # Per-axis tolerance: max(25% of flat
                            # extent on that axis, 1 m absolute).
                            # The 1 m floor catches very short
                            # axes (thickness ≈ 0) where 25% would
                            # be zero.
                            slack = _np.maximum(0.25 * flat_extent, 1.0)
                            outside = (surf_centre < flat_min - slack) | (surf_centre > flat_max + slack)
                            mismatch = None
                            if bool(outside.any()):
                                axes = [a for a, o in enumerate(outside) if o]
                                offsets = [
                                    float(max(flat_min[a] - surf_centre[a], surf_centre[a] - flat_max[a])) for a in axes
                                ]
                                mismatch = (
                                    f"surface centre outside flat bbox on axis {axes} "
                                    f"by {[round(o, 2) for o in offsets]} m "
                                    f"(slack {[round(s, 2) for s in slack[axes]]} m)"
                                )
                            else:
                                # Per-axis size ratio. ``flat_extent`` may
                                # be 0 along the thickness axis; clamp to
                                # a 1 mm floor so the ratio is defined.
                                ratio = surf_extent / _np.maximum(flat_extent, 1e-3)
                                max_ratio = float(ratio.max())
                                if max_ratio > 5.0:
                                    worst = int(_np.argmax(ratio))
                                    mismatch = (
                                        f"surface extent {surf_extent[worst]:.1f} m "
                                        f"on axis {worst} vs flat {flat_extent[worst]:.1f} m "
                                        f"({max_ratio:.1f}× larger — likely stretched / deformed)"
                                    )
                            if mismatch:
                                logger.warning(
                                    "PlateCurved %r: %s — using flat representation",
                                    name,
                                    mismatch,
                                )
                                plane = _fit_best_fit_plane(fallback_pts)
                                yield _plate_from_face(
                                    name,
                                    _project_onto_plane(fallback_pts, plane) if plane else list(fallback_pts),
                                    _project_edge_curves_onto_plane(edge_curves_d.get(face_ref), plane),
                                    t,
                                    normal_for_face(face_ref),
                                    mat=mat,
                                    metadata=dict(props=dict(gxml_face_ref=face_ref)),
                                    parent=parent,
                                )
                                continue
                    except Exception:
                        pass
                pc = PlateCurved(
                    name,
                    sat_data,
                    t=t,
                    mat=mat,
                    metadata=dict(
                        props=dict(
                            gxml_face_ref=face_ref,
                            gxml_sense_flag=_sense_against_face(sat_data, desired_normal, sense),
                        )
                    ),
                    parent=parent,
                )
                # Attach the planar fallback points (the SAT face's
                # loop-edge endpoints) if available. Tessellator
                # checks for ``_flat_fallback_pts`` when the BSpline
                # face fails to construct and degrades to a flat
                # plate using these points instead of dropping the
                # plate entirely. Restores the pre-exppc-fix
                # behaviour for plates whose advanced-face succeeds
                # but downstream OCC face construction fails the
                # strict pcurve guard.
                if fallback_pts is not None:
                    pc._flat_fallback_pts = fallback_pts
                    # Carry the analytic arc specs too, so if the tessellator degrades this curved
                    # shell to its flat fallback it still draws the boundary arcs as real arcs rather
                    # than chords. Materialized only if the fallback fires — never sampled up front.
                    pc._flat_fallback_edge_curves = edge_curves_d.get(face_ref)
                yield pc
                continue

            if sat_data is None:
                # AdvancedFace conversion was rejected upstream
                # (typically the SAT bbox sanity check in
                # ``get_face_surface``, or an exppc chain that
                # terminates in a 1D curve implying a swept/ruled
                # surface we don't synthesise yet). Fall back to the
                # flat-plate corner points if the SAT face has them —
                # the wire's 3D vertex chain is a reliable independent
                # reference. Pre-project the corners onto their SVD
                # best-fit plane so curved-shell wires (whose corners
                # are typically non-coplanar) don't end up tilted by
                # the naive 3-point plane fit in
                # ``Plate.from_3d_points``.
                fb = flat_fallback_d.get(face_ref)
                if fb and len(fb) >= 3:
                    try:
                        plane = _fit_best_fit_plane(fb)
                        projected = _project_onto_plane(fb, plane) if plane else list(fb)
                        yield _plate_from_face(
                            name,
                            projected,
                            _project_edge_curves_onto_plane(edge_curves_d.get(face_ref), plane),
                            t,
                            normal_for_face(face_ref),
                            mat=mat,
                            metadata=dict(props=dict(gxml_face_ref=face_ref)),
                            parent=parent,
                        )
                    except Exception as e:
                        logger.error(f"Failed converting flat-fallback plate {name} due to {e}")
                    continue
                logger.debug(f'Unable to find face_ref="{face_ref}"')
                continue

            try:
                yield _plate_from_face(
                    name,
                    sat_data,
                    edge_curves_d.get(face_ref),
                    t,
                    normal_for_face(face_ref),
                    mat=mat,
                    metadata=dict(props=dict(gxml_face_ref=face_ref)),
                    parent=parent,
                )
            except Exception as e:
                logger.error(f"Failed converting plate {name} due to {e}")
                continue

        return

    # Inline polygon geometry (no SAT face refs).
    poly_elems = list(plate_elem.findall(".//geometry//sheet//polygons//polygon"))
    for i, poly in enumerate(poly_elems, start=1):
        pts = _read_inline_polygon(poly)
        if len(pts) < 3:
            logger.debug(f'Plate "{base_name}" polygon #{i} has < 3 points, skipping')
            continue

        name = base_name if i == 1 else f"{base_name}_{i:02d}"
        try:
            yield _plate_from_3d_points(
                name,
                pts,
                t,
                desired_normal,
                mat=mat,
                metadata=dict(props=dict(gxml_polygon_index=i)),
                parent=parent,
            )
        except Exception as e:
            logger.error(f"Failed converting polygon plate {name} due to {e}")
            continue
