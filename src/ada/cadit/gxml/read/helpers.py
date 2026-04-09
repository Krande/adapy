from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.plates import PlateCurved
from ada.cadit.gxml.read.read_beams import el_to_beam
from ada.cadit.gxml.read.read_materials import get_materials
from ada.cadit.gxml.read.read_sections import get_sections
from ada.config import Config, logger
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


def yield_plate_elems_to_plate(plate_elem, parent, sat_ref_d, thick_map):
    from ada import Plate

    def _round_point(pt, ndigits=9):
        return tuple(round(float(x), ndigits) for x in pt)

    def _canonical_edge_key(p1, p2, ndigits=9):
        k1 = _round_point(p1, ndigits)
        k2 = _round_point(p2, ndigits)
        return (k1, k2) if k1 <= k2 else (k2, k1)

    def _remove_duplicate_last_point_3d(pts, tol_digits=9):
        if len(pts) >= 2 and _round_point(pts[0], tol_digits) == _round_point(pts[-1], tol_digits):
            return pts[:-1]
        return pts

    def _iter_edges_closed(pts):
        n = len(pts)
        for i in range(n):
            yield pts[i], pts[(i + 1) % n]

    def _project_points_to_2d(points):
        spans = []
        for axis in range(3):
            vals = [p[axis] for p in points]
            spans.append(max(vals) - min(vals))
        drop_axis = min(range(3), key=lambda i: spans[i])

        def to_2d(p):
            if drop_axis == 0:
                return (p[1], p[2])
            elif drop_axis == 1:
                return (p[0], p[2])
            else:
                return (p[0], p[1])

        return [to_2d(p) for p in points], drop_axis

    def _polygon_scale_2d(pts2d):
        xs = [p[0] for p in pts2d]
        ys = [p[1] for p in pts2d]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        return max(dx, dy, 1.0)

    def _remove_near_collinear_points_3d(pts3d, tol_factor=1e-8):
        if len(pts3d) < 4:
            return pts3d

        pts2d, _ = _project_points_to_2d(pts3d)
        scale = _polygon_scale_2d(pts2d)
        tol = tol_factor * scale * scale

        cleaned = []
        n = len(pts3d)

        for i in range(n):
            p_prev = pts2d[i - 1]
            p_curr = pts2d[i]
            p_next = pts2d[(i + 1) % n]

            cross = (p_curr[0] - p_prev[0]) * (p_next[1] - p_curr[1]) - (p_curr[1] - p_prev[1]) * (
                p_next[0] - p_curr[0]
            )

            if abs(cross) > tol:
                cleaned.append(pts3d[i])

        return cleaned if len(cleaned) >= 3 else pts3d

    def _all_points_coplanar(point_sets, tol=1e-6):
        pts = [tuple(map(float, p)) for pts in point_sets for p in pts]

        unique = []
        seen = set()
        for p in pts:
            key = tuple(round(x, 9) for x in p)
            if key not in seen:
                seen.add(key)
                unique.append(p)

        if len(unique) < 4:
            return True

        p0 = unique[0]
        v1 = None
        v2 = None
        n = len(unique)

        for i in range(1, n):
            a = unique[i]
            va = (a[0] - p0[0], a[1] - p0[1], a[2] - p0[2])
            if (va[0] ** 2 + va[1] ** 2 + va[2] ** 2) <= tol * tol:
                continue

            for j in range(i + 1, n):
                b = unique[j]
                vb = (b[0] - p0[0], b[1] - p0[1], b[2] - p0[2])

                cross = (
                    va[1] * vb[2] - va[2] * vb[1],
                    va[2] * vb[0] - va[0] * vb[2],
                    va[0] * vb[1] - va[1] * vb[0],
                )
                cross_len2 = cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2
                if cross_len2 > tol * tol:
                    v1 = va
                    v2 = vb
                    break

            if v1 is not None:
                break

        if v1 is None or v2 is None:
            return False

        normal = (
            v1[1] * v2[2] - v1[2] * v2[1],
            v1[2] * v2[0] - v1[0] * v2[2],
            v1[0] * v2[1] - v1[1] * v2[0],
        )

        norm_len = (normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2) ** 0.5
        if norm_len <= tol:
            return False

        normal = (normal[0] / norm_len, normal[1] / norm_len, normal[2] / norm_len)

        for p in unique:
            vp = (p[0] - p0[0], p[1] - p0[1], p[2] - p0[2])
            dist = abs(vp[0] * normal[0] + vp[1] * normal[1] + vp[2] * normal[2])
            if dist > tol:
                return False

        return True

    def _merge_face_point_sets(face_point_sets, ndigits=9):
        """
        Merge multiple coplanar planar face loops by canceling shared internal edges.
        Returns one outer loop as 3D points, or None if merge is not cleanly possible.
        """
        edge_counts = {}
        point_repr = {}

        valid_sets = []
        for pts in face_point_sets:
            pts = list(pts)
            pts = _remove_duplicate_last_point_3d(pts, ndigits)
            if len(pts) < 3:
                continue
            valid_sets.append(pts)

            for p1, p2 in _iter_edges_closed(pts):
                k1 = _round_point(p1, ndigits)
                k2 = _round_point(p2, ndigits)
                if k1 == k2:
                    continue

                ekey = _canonical_edge_key(p1, p2, ndigits)
                edge_counts[ekey] = edge_counts.get(ekey, 0) + 1

                if k1 not in point_repr:
                    point_repr[k1] = p1
                if k2 not in point_repr:
                    point_repr[k2] = p2

        if not valid_sets:
            return None

        boundary_edges = [ekey for ekey, count in edge_counts.items() if count == 1]
        if len(boundary_edges) < 3:
            return None

        adjacency = {}
        for k1, k2 in boundary_edges:
            adjacency.setdefault(k1, []).append(k2)
            adjacency.setdefault(k2, []).append(k1)

        if any(len(neigh) != 2 for neigh in adjacency.values()):
            return None

        def _edge_key_from_nodes(a, b):
            return (a, b) if a <= b else (b, a)

        unused_edges = set(boundary_edges)
        loops = []

        while unused_edges:
            start_edge = next(iter(unused_edges))
            start = start_edge[0]

            loop = [start]
            current = start
            prev = None

            while True:
                neigh = adjacency[current]

                if prev is None:
                    nxt = neigh[0]
                else:
                    nxt = neigh[0] if neigh[0] != prev else neigh[1]

                ekey = _edge_key_from_nodes(current, nxt)
                if ekey not in unused_edges:
                    return None

                unused_edges.remove(ekey)
                prev, current = current, nxt

                if current == start:
                    break

                loop.append(current)

            loops.append(loop)

        if len(loops) != 1:
            return None

        merged_points = [point_repr[k] for k in loops[0]]
        merged_points = _remove_near_collinear_points_3d(merged_points)

        if len(merged_points) < 3:
            return None

        return merged_points

    base_name = plate_elem.attrib["name"]
    mat = parent.materials.get_by_name(plate_elem.attrib["material_ref"])
    t = thick_map.get(plate_elem.attrib.get("thickness_ref"))

    # --- 1) SAT face references ---
    face_elems = list(plate_elem.findall(".//face"))
    if face_elems:
        face_refs = [res.attrib["face_ref"] for res in face_elems]

        # Merge multi-face planar plate/shells if all referenced SAT geometries
        # are point loops and all points are coplanar.
        if len(face_elems) > 1:
            face_point_sets = []
            merge_possible = True

            for face_ref in face_refs:
                sat_data = sat_ref_d.get(face_ref, None)

                if sat_data is None:
                    merge_possible = False
                    break

                if isinstance(sat_data, Geometry):
                    merge_possible = False
                    break

                if not isinstance(sat_data, (list, tuple)) or len(sat_data) < 3:
                    merge_possible = False
                    break

                face_point_sets.append(list(sat_data))

            if merge_possible and _all_points_coplanar(face_point_sets):
                merged_points = _merge_face_point_sets(face_point_sets)
                if merged_points is not None:
                    try:
                        pl = Plate.from_3d_points(
                            base_name,
                            merged_points,
                            t,
                            mat=mat,
                            metadata=dict(props=dict(gxml_face_refs=face_refs)),
                            parent=parent,
                        )
                        yield pl
                        return
                    except BaseException as e:
                        logger.error(f"Failed converting merged plate {base_name} due to {e}")
                        # fall through to per-face behavior

        # Original per-face behavior
        name = base_name
        for i, res in enumerate(face_elems, start=1):
            face_ref = res.attrib["face_ref"]

            if i > 1:
                name = f"{base_name}_{i:02d}"
            else:
                name = base_name

            sat_data = sat_ref_d.get(face_ref, None)

            if isinstance(sat_data, Geometry) and Config().gxml_import_advanced_faces is True:
                yield PlateCurved(
                    name,
                    sat_data,
                    t=t,
                    mat=mat,
                    metadata=dict(props=dict(gxml_face_ref=face_ref)),
                    parent=parent,
                )
                continue

            if sat_data is None:
                logger.debug(f'Unable to find face_ref="{face_ref}"')
                continue

            try:
                pl = Plate.from_3d_points(
                    name,
                    sat_data,
                    t,
                    mat=mat,
                    metadata=dict(props=dict(gxml_face_ref=face_ref)),
                    parent=parent,
                )
            except BaseException as e:
                logger.error(f"Failed converting plate {name} due to {e}")
                continue

            yield pl

        return

    # --- 2) Inline polygon geometry (no SAT face refs) ---
    poly_elems = list(plate_elem.findall(".//geometry//sheet//polygons//polygon"))
    if not poly_elems:
        return

    for i, poly in enumerate(poly_elems, start=1):
        pts = []
        for p in poly.findall("./position"):
            pts.append((float(p.attrib["x"]), float(p.attrib["y"]), float(p.attrib["z"])))

        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts = pts[:-1]

        if len(pts) < 3:
            logger.debug(f'Plate "{base_name}" polygon #{i} has < 3 points, skipping')
            continue

        name = base_name if i == 1 else f"{base_name}_{i:02d}"

        try:
            pl = Plate.from_3d_points(
                name,
                pts,
                t,
                mat=mat,
                metadata=dict(props=dict(gxml_polygon_index=i)),
                parent=parent,
            )
        except BaseException as e:
            logger.error(f"Failed converting polygon plate {name} due to {e}")
            continue

        yield pl
