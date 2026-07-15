from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import numpy as np

from ...sat.write.writer import SatWriter

if TYPE_CHECKING:
    from ada import Part, Plate


def thickness_name(t: float) -> str:
    """Canonical Genie thickness-property name for a plate thickness (m).

    Shared by the DOM writer (:func:`add_plates`) and the streaming writer so
    both mint identical ``thkNNN`` references for the same thickness.
    """
    thick_mm = t * 1000
    if thick_mm.is_integer():
        thick_mm_str = f"{int(thick_mm):03d}"
    else:
        thick_mm_str = f"{int(thick_mm):03d}_{str(thick_mm).split('.')[1]}"
    return f"thk{thick_mm_str}"


def add_plate_sat(plate: Plate, thck_name: str, structures_elem, sw: SatWriter):
    structure = ET.SubElement(structures_elem, "structure")
    flat_plate = ET.SubElement(
        structure, "flat_plate", {"name": plate.name, "thickness_ref": thck_name, "material_ref": plate.material.name}
    )
    local_sys = ET.SubElement(flat_plate, "local_system")
    ET.SubElement(
        local_sys,
        "vector",
        {"x": str(plate.poly.normal[0]), "y": str(plate.poly.normal[1]), "z": str(plate.poly.normal[2]), "dir": "z"},
    )
    ET.SubElement(flat_plate, "front")
    ET.SubElement(flat_plate, "back")
    ET.SubElement(flat_plate, "segmentation")
    geometry = ET.SubElement(flat_plate, "geometry")
    sheet = ET.SubElement(geometry, "sheet")
    sat_reference = ET.SubElement(sheet, "sat_reference")
    # A plate resolves to several faces once the imprint pass splits it at the
    # T-junctions with its neighbours, so every name in the map gets an element
    # (a densely stiffened panel reaches ten).
    for face_ref in sw.face_map.get(plate.guid, []):
        ET.SubElement(sat_reference, "face", {"face_ref": face_ref})


def add_plate_polygon_data(
    name: str, outline_global, normal, thck_name: str, material_name: str, structures_elem: ET.Element
):
    """Emit a ``<flat_plate>`` polygon from raw data (no Plate object).

    The object-free FEM-shell face source (:mod:`ada.fem.formats.mesh_faces`)
    yields global outlines + normal + refs directly, so the streaming writer can
    emit plates without ever materialising a :class:`Plate`. Produces the same
    element shape as :func:`add_plate_polygon` (whose object path round-trips its
    local poly back to these same global positions)."""
    structure = ET.SubElement(structures_elem, "structure")
    flat_plate = ET.SubElement(
        structure, "flat_plate", {"name": name, "thickness_ref": thck_name, "material_ref": material_name}
    )
    local_sys = ET.SubElement(flat_plate, "local_system")
    ET.SubElement(local_sys, "vector", {"x": str(normal[0]), "y": str(normal[1]), "z": str(normal[2]), "dir": "z"})
    ET.SubElement(flat_plate, "front")
    ET.SubElement(flat_plate, "back")
    ET.SubElement(flat_plate, "segmentation")

    geometry = ET.SubElement(flat_plate, "geometry")
    sheet = ET.SubElement(geometry, "sheet")
    polygons = ET.SubElement(sheet, "polygons")
    polygon = ET.SubElement(polygons, "polygon")
    for pt in outline_global:
        ET.SubElement(polygon, "position", {"x": str(pt[0]), "y": str(pt[1]), "z": str(pt[2])})


def add_curved_shell_sat(plate, thck_name: str, structures_elem: ET.Element, face_refs: list[str]):
    """Emit a :class:`PlateCurved` as a ``<curved_shell>`` naming its SAT face.

    The element Genie itself writes for a curved plate, and the only form that
    carries the B-spline patch rather than an outline: the geometry lives in
    the SAT blob and the XML just points at the face by name. Shaped after a
    Genie-authored hull export, whose curved shells carry exactly this."""
    structure = ET.SubElement(structures_elem, "structure")
    curved_shell = ET.SubElement(
        structure, "curved_shell", {"name": plate.name, "thickness_ref": thck_name, "material_ref": plate.material.name}
    )
    ET.SubElement(curved_shell, "segmentation")
    ET.SubElement(curved_shell, "front")
    ET.SubElement(curved_shell, "back")
    local_sys = ET.SubElement(curved_shell, "local_system")
    # A curved shell has no single normal to hand a <vector>; Genie orients it
    # with a sense flag against the face's own surface normal instead. It is
    # authored data — mostly false (4413 of 4746 in a hull export), so hardcoding
    # true here drew all but a handful of the plates inside-out.
    sense = plate.metadata.get("props", {}).get("gxml_sense_flag", True)
    ET.SubElement(local_sys, "sense_flag", {"sense": "true" if sense else "false"})
    geometry = ET.SubElement(curved_shell, "geometry")
    sheet = ET.SubElement(geometry, "sheet")
    sat_reference = ET.SubElement(sheet, "sat_reference")
    for face_ref in face_refs:
        ET.SubElement(sat_reference, "face", {"face_ref": face_ref})


def add_plate_curved_polygon(plate, thck_name: str, structures_elem: ET.Element) -> bool:
    """Emit a :class:`PlateCurved` as a ``<flat_plate>`` boundary polygon.

    The fallback for a curved face the SAT writer cannot author (see
    :func:`add_curved_shell_sat` for the real path): the face degrades to its
    boundary polygon, position- and extent-faithful with the curvature lost.
    Uses the reader-attached flat-fallback points when present (the SAT
    loop-edge endpoints), else the face's boundary nodes. Returns False when no
    usable boundary can be derived, so the caller can log the drop."""
    pts = getattr(plate, "_flat_fallback_pts", None)
    if not pts:
        pts = [n.p for n in plate.nodes]
    if pts is None or len(pts) < 3:
        return False
    arr = np.asarray([[float(p[0]), float(p[1]), float(p[2])] for p in pts], dtype=float)
    # Newell's method: robust polygon normal for an ordered, possibly
    # non-planar boundary loop.
    nrm = np.zeros(3)
    for i in range(len(arr)):
        a, b = arr[i], arr[(i + 1) % len(arr)]
        nrm[0] += (a[1] - b[1]) * (a[2] + b[2])
        nrm[1] += (a[2] - b[2]) * (a[0] + b[0])
        nrm[2] += (a[0] - b[0]) * (a[1] + b[1])
    length = np.linalg.norm(nrm)
    if length < 1e-12:
        return False
    add_plate_polygon_data(plate.name, arr, nrm / length, thck_name, plate.material.name, structures_elem)
    return True


def add_plate_polygon(plate: Plate, thck_name: str, structures_elem: ET.Element):
    outline_global, normal = plate.outline_global()
    add_plate_polygon_data(plate.name, outline_global, normal, thck_name, plate.material.name, structures_elem)


def add_plates(structure_domain: ET.Element, part: Part, sw: SatWriter):
    from ada import Plate

    thickness = {}
    properties = structure_domain.find("./properties")
    thickness_elem = ET.SubElement(properties, "thicknesses")
    structures_elem = structure_domain.find("./structures")

    for plate in part.get_all_physical_objects(by_type=Plate):
        if plate.t not in thickness:
            thickness[plate.t] = thickness_name(plate.t)
            tck_elem = ET.Element("thickness", {"name": thickness[plate.t], "default": "true"})
            tck_elem.append(ET.Element("constant_thickness", {"th": str(plate.t)}))
            thickness_elem.append(tck_elem)

        thck_name = thickness[plate.t]

        if sw is not None:
            add_plate_sat(plate, thck_name, structures_elem, sw)
        else:
            add_plate_polygon(plate, thck_name, structures_elem)

    from ada.api.plates import PlateCurved
    from ada.config import logger

    for plate in part.get_all_physical_objects(by_type=PlateCurved):
        if plate.t not in thickness:
            thickness[plate.t] = thickness_name(plate.t)
            tck_elem = ET.Element("thickness", {"name": thickness[plate.t], "default": "true"})
            tck_elem.append(ET.Element("constant_thickness", {"th": str(plate.t)}))
            thickness_elem.append(tck_elem)

        face_refs = sw.face_map.get(plate.guid, []) if sw is not None else []
        if face_refs:
            add_curved_shell_sat(plate, thickness[plate.t], structures_elem, face_refs)
        elif not add_plate_curved_polygon(plate, thickness[plate.t], structures_elem):
            # No SAT face and no usable boundary — nothing left to emit.
            logger.warning(f"gxml-write: PlateCurved {plate.name!r} has no usable boundary; dropped")
