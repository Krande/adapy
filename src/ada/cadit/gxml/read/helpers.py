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

    base_name = plate_elem.attrib["name"]
    mat = parent.materials.get_by_name(plate_elem.attrib["material_ref"])
    t = thick_map.get(plate_elem.attrib.get("thickness_ref"))

    # --- 1) Existing path: SAT face references ---
    face_elems = list(plate_elem.findall(".//face"))
    if face_elems:
        name = base_name
        for i, res in enumerate(face_elems, start=1):
            face_ref = res.attrib["face_ref"]

            if i > 1:
                name = f"{base_name}_{i:02d}"

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

        return  # done

    # --- 2) New path: inline polygon geometry (no SAT face refs) ---
    # Typical for <flat_plate> with <geometry><sheet><polygons><polygon><position .../></polygon>...
    poly_elems = list(plate_elem.findall(".//geometry//sheet//polygons//polygon"))
    if not poly_elems:
        return  # nothing we recognize

    for i, poly in enumerate(poly_elems, start=1):
        pts = []
        for p in poly.findall("./position"):
            pts.append((float(p.attrib["x"]), float(p.attrib["y"]), float(p.attrib["z"])))

        # If polygon is closed (last == first), drop the duplicate last point
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
