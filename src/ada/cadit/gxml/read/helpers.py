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


def yield_plate_elems_to_plate(plate_elem, parent, sat_ref_d, thick_map, flat_fallback_d=None):
    from ada import Plate

    base_name = plate_elem.attrib["name"]
    mat = parent.materials.get_by_name(plate_elem.attrib["material_ref"])
    t = thick_map.get(plate_elem.attrib.get("thickness_ref"))
    if flat_fallback_d is None:
        flat_fallback_d = {}

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
                        yield Plate.from_3d_points(
                            base_name,
                            merged_points,
                            t,
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
                if fallback_pts is not None and len(fallback_pts) >= 3:
                    try:
                        import numpy as _np
                        flat_arr = _np.array([list(p)[:3] for p in fallback_pts])
                        flat_centre = flat_arr.mean(axis=0)
                        flat_diag = float(_np.linalg.norm(flat_arr.max(axis=0) - flat_arr.min(axis=0)))
                        # Sample the AdvancedFace surface centre in
                        # 3D. Different surface types expose
                        # different centre-evaluation APIs; we only
                        # care about a coarse 3D point.
                        surf = getattr(sat_data.geometry, "face_surface", None)
                        cps = getattr(surf, "control_points_list", None)
                        if cps:
                            xs, ys, zs = [], [], []
                            for row in cps:
                                for cp in row:
                                    xs.append(cp[0]); ys.append(cp[1]); zs.append(cp[2])
                            surf_centre = _np.array([
                                sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs),
                            ])
                            offset = float(_np.linalg.norm(surf_centre - flat_centre))
                            tol = max(2.0 * flat_diag, 5.0)
                            if offset > tol:
                                logger.warning(
                                    "PlateCurved %r: BSpline surface centre %.1f m from "
                                    "flat-fallback centroid (tol %.1f m, flat diag %.1f m); "
                                    "exppc surface-peel mismatch — using flat representation",
                                    name, offset, tol, flat_diag,
                                )
                                yield Plate.from_3d_points(
                                    name, fallback_pts, t, mat=mat,
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
                    metadata=dict(props=dict(gxml_face_ref=face_ref)),
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
                yield pc
                continue

            if sat_data is None:
                logger.debug(f'Unable to find face_ref="{face_ref}"')
                continue

            try:
                yield Plate.from_3d_points(
                    name,
                    sat_data,
                    t,
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
            yield Plate.from_3d_points(
                name,
                pts,
                t,
                mat=mat,
                metadata=dict(props=dict(gxml_polygon_index=i)),
                parent=parent,
            )
        except Exception as e:
            logger.error(f"Failed converting polygon plate {name} due to {e}")
            continue
