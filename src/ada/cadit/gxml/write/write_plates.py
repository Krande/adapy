from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ...sat.write.writer import SatWriter

if TYPE_CHECKING:
    from ada import Part, Plate


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


def add_plate_polygon(plate: Plate, thck_name: str, structures_elem: ET.Element):
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

    # Add polygon geometry
    geometry = ET.SubElement(flat_plate, "geometry")
    sheet = ET.SubElement(geometry, "sheet")
    polygons = ET.SubElement(sheet, "polygons")
    polygon = ET.SubElement(polygons, "polygon")

    # plate.poly.points3d

    for pt in plate.poly.points3d:
        ET.SubElement(polygon, "position", {"x": str(pt[0]), "y": str(pt[1]), "z": str(pt[2])})

    # ET.SubElement(sat_reference, "face", {"face_ref": sw.face_map.get(plate.guid)})


def add_plates(structure_domain: ET.Element, part: Part, sw: SatWriter):
    from ada import Plate

    thickness = {}
    properties = structure_domain.find("./properties")
    thickness_elem = ET.SubElement(properties, "thicknesses")
    structures_elem = structure_domain.find("./structures")

    for plate in part.get_all_physical_objects(by_type=Plate):
        if plate.t not in thickness:
            thickness[plate.t] = f"Tck{len(thickness)+1}"
            tck_elem = ET.Element("thickness", {"name": thickness[plate.t], "default": "true"})
            tck_elem.append(ET.Element("constant_thickness", {"th": str(plate.t)}))
            thickness_elem.append(tck_elem)

        thck_name = thickness[plate.t]

        if sw is not None:
            add_plate_sat(plate, thck_name, structures_elem, sw)
        else:
            add_plate_polygon(plate, thck_name, structures_elem)
