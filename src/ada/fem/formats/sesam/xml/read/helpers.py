import xml.etree.ElementTree as ET

from ada.config import get_logger
from ada.fem.formats.sesam.xml.read.read_beams import el_to_beam
from ada.fem.formats.sesam.xml.read.read_materials import get_materials
from ada.fem.formats.sesam.xml.read.read_sections import get_sections

logger = get_logger()


def iter_beams_from_xml(xml_path):
    from ada import Part

    xml_root = ET.parse(str(xml_path)).getroot()
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    p = Part("tmp")
    p._sections = get_sections(xml_root, p)
    p._materials = get_materials(xml_root, p)
    for bm_el in all_beams:
        yield from el_to_beam(bm_el, p)


def apply_mass_density_factors(root, p):
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

    mat = parent.materials.get_by_name(plate_elem.attrib["material_ref"])
    for i, res in enumerate(plate_elem.findall(".//face"), start=1):
        face_ref = res.attrib["face_ref"]
        points = sat_ref_d.get(face_ref, None)
        if points is None:
            logger.warning(f'Unable to find face_ref="{face_ref}"')
            continue

        name = plate_elem.attrib["name"]
        if i > 1:
            name += f"_{i:02d}"

        t = thick_map.get(plate_elem.attrib["thickness_ref"])
        try:
            pl = Plate(
                name,
                points,
                t,
                mat=mat,
                metadata=dict(props=dict(gxml_face_ref=face_ref)),
                use3dnodes=True,
                parent=parent,
            )
        except BaseException as e:
            logger.error(f"Failed converting plate {name} due to {e}")
            continue
        yield pl
