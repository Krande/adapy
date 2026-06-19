from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import numpy as np

from ada.api.transforms import Placement

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
    ET.SubElement(sat_reference, "face", {"face_ref": sw.face_map.get(plate.guid)})


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
    ET.SubElement(
        local_sys, "vector", {"x": str(normal[0]), "y": str(normal[1]), "z": str(normal[2]), "dir": "z"}
    )
    ET.SubElement(flat_plate, "front")
    ET.SubElement(flat_plate, "back")
    ET.SubElement(flat_plate, "segmentation")

    geometry = ET.SubElement(flat_plate, "geometry")
    sheet = ET.SubElement(geometry, "sheet")
    polygons = ET.SubElement(sheet, "polygons")
    polygon = ET.SubElement(polygons, "polygon")
    for pt in outline_global:
        ET.SubElement(polygon, "position", {"x": str(pt[0]), "y": str(pt[1]), "z": str(pt[2])})


def add_plate_polygon(plate: Plate, thck_name: str, structures_elem: ET.Element):
    abs_place = plate.placement.get_absolute_placement(include_rotations=True)
    ident = Placement()  # identity place
    outline_global = [
        abs_place.transform_array_from_other_place(np.asarray([pt], dtype=float), ident, ignore_translation=False)[0]
        for pt in plate.poly.points3d
    ]
    add_plate_polygon_data(plate.name, outline_global, plate.poly.normal, thck_name, plate.material.name, structures_elem)


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
