import base64
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from ada import Part, Plate
from ada.concepts.containers import Plates
from ada.sat.reader import get_plates_from_satd


def get_plates(xml_root: ET.Element, parent: Part) -> Plates:
    sat_ref_d = dict()
    for sat_geometry_el in xml_root.findall(".//sat_embedded"):
        sat_ref_d.update(extract_sat_data(sat_geometry_el))

    for sat_geometry_seq in xml_root.findall(".//sat_embedded_sequence"):
        sat_ref_d.update(extract_sat_data(sat_geometry_seq))

    thick_map = dict()
    for thickn in xml_root.findall(".//thickness"):
        res = thickn.find(".//constant_thickness")
        thick_map[thickn.attrib["name"]] = float(res.attrib["th"])

    plates = []
    for plate_elem in xml_root.findall(".//flat_plate") + xml_root.findall(".//curved_shell"):
        mat = parent.materials.get_by_name(plate_elem.attrib["material_ref"])
        for i, res in enumerate(plate_elem.findall(".//face"), start=1):
            face_ref = res.attrib["face_ref"]
            points = sat_ref_d.get(face_ref, None)
            if points is None:
                logging.warning(f'Unable to find face_ref="{face_ref}"')
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
                logging.error(f"Failed converting plate {name} due to {e}")
                continue

            plates.append(pl)

    return Plates(plates, parent)


def extract_sat_data(sat_el: ET.Element) -> dict:
    if sat_el.tag == "sat_embedded":
        text = sat_el.text
        data = base64.b64decode(text)
    elif sat_el.tag == "sat_embedded_sequence":
        data = b""
        for res in sat_el.findall(".//cdata_segment"):
            res = base64.b64decode(res.text)
            data += res
    else:
        raise NotImplementedError(f'SAT el Tag type "{sat_el.tag}" is not yet added')
    satd = sat_data_text_to_dict(data)
    return get_plates_from_satd(satd)


def sat_data_text_to_dict(data: bytes) -> dict:
    byio = BytesIO(data)
    zipdata = zipfile.ZipFile(byio)
    res = {name: zipdata.read(name) for name in zipdata.namelist()}
    if len(res.keys()) != 1:
        raise NotImplementedError("No support for binary zip data containing multipart SAT file yet")

    sat_data = str(res["b64temp.sat"], encoding="utf-8")

    sat_dict = dict()
    for res in re.finditer(
        r"^-(?P<id>[0-9]{1,7}) (?P<name>.*?) (?P<bulk>.*?) #",
        sat_data,
        re.MULTILINE | re.DOTALL,
    ):
        d = res.groupdict()
        sat_dict[d["id"]] = (d["name"], *d["bulk"].split())

    return sat_dict
