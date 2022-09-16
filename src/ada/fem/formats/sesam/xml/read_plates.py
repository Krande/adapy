import base64
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from ada import Part, Plate
from ada.concepts.containers import Plates


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
            pl = Plate(
                name,
                points,
                t,
                metadata=dict(props=dict(gxml_face_ref=face_ref)),
                use3dnodes=True,
                parent=parent,
            )

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


def get_plates_from_satd(satd: dict) -> dict:
    plate_geom = dict()

    for face in filter(lambda x: x[0] == "face", satd.values()):
        try:
            res = get_face_from_satd(face, satd)
        except KeyError as e:
            logging.warning(f"Unable to import face due to {e}")
            continue
        except InsufficientPointsError as e:
            logging.warning(f"Unable to import face due to {e}")
            continue
        plate_geom.update(res)

    return plate_geom


class InsufficientPointsError(Exception):
    pass


def get_face_from_satd(face: tuple, satd: dict):
    # Face row
    name_idx = 1
    loop_idx = 6

    # Loop row
    coedge_ref = 6

    face_ref = get_value_from_satd(face[name_idx], satd)[-1]

    loop = get_value_from_satd(face[loop_idx], satd)
    coedge_start_id = loop[coedge_ref]
    coedge_first = get_value_from_satd(coedge_start_id, satd)

    coedge_first_direction = str(coedge_first[-3])

    # Coedge row
    next_coedge_idx = 5 if coedge_first_direction == "forward" else 6

    next_coedge = True
    coedge_next_id = coedge_first[next_coedge_idx]
    edges = [coedge_first]

    max_iter = 100
    i = 0
    while next_coedge is True:
        coedge = get_value_from_satd(coedge_next_id, satd)
        edges.append(coedge)

        coedge_next_id = coedge[next_coedge_idx]
        if coedge_next_id == coedge_start_id:
            next_coedge = False

        i += 1
        if i > max_iter:
            raise ValueError(f"Found {i} points which is over max={max_iter}")

    p1, p2 = get_points_from_edge(coedge_first, satd)

    points = [p1, p2]

    for coedge in edges:
        p1, p2 = get_points_from_edge(coedge, satd)
        edge_direction = str(coedge[-3])
        if edge_direction == "forward":
            p = p2
        else:
            p = p1
        if p not in points:
            points.append(p)

    if len(points) < 3:
        raise InsufficientPointsError("Plates cannot have < 3 points")

    if coedge_first_direction == "reversed":
        points.reverse()

    return {face_ref: points}


def get_points_from_edge(coedge: tuple, satd: dict):
    # Coedge row
    edge_ref = 8

    # Edge row
    vert1_idx = 5
    vert2_idx = 7
    # edge_type_idx = 8

    # Vertex row
    p_idx = -1

    edge = get_value_from_satd(coedge[edge_ref], satd)
    vert1 = get_value_from_satd(edge[vert1_idx], satd)
    vert2 = get_value_from_satd(edge[vert2_idx], satd)
    # edge_type = get_value_from_satd(edge[edge_type_idx], satd)
    p1 = get_value_from_satd(vert1[p_idx], satd)
    p2 = get_value_from_satd(vert2[p_idx], satd)
    n1 = tuple([float(x) for x in p1[-3:]])
    n2 = tuple([float(x) for x in p2[-3:]])
    return n1, n2


def get_value_from_satd(val_str: str, satd: dict) -> tuple:
    return satd[val_str.replace("$", "")]


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
