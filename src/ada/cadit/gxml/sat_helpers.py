import base64
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO


def xml_elem_to_sat_text(sat_el: ET.Element) -> str:
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

    byio = BytesIO(data)
    try:
        zipdata = zipfile.ZipFile(byio)
    except zipfile.BadZipFile:
        return str(data, encoding="utf-8")

    res = {name: zipdata.read(name) for name in zipdata.namelist()}
    if len(res.keys()) != 1:
        raise NotImplementedError("No support for binary zip data containing multipart SAT file yet")

    return str(res["b64temp.sat"], encoding="utf-8").replace("\r", "")


def write_xml_sat_text_to_file(xml_file, out_file):
    xml_root = ET.parse(str(xml_file)).getroot()
    with open(out_file, "w") as f:
        for sat_geometry_el in xml_root.iterfind(".//sat_embedded"):
            f.write(xml_elem_to_sat_text(sat_geometry_el))
        for sat_geometry_seq in xml_root.iterfind(".//sat_embedded_sequence"):
            f.write(xml_elem_to_sat_text(sat_geometry_seq))


def get_sat_text_from_xml(xml_file):
    xml_root = ET.parse(str(xml_file)).getroot()
    sat_text = ""

    for sat_geometry_el in xml_root.findall(".//sat_embedded"):
        sat_text += xml_elem_to_sat_text(sat_geometry_el)

    for sat_geometry_seq in xml_root.findall(".//sat_embedded_sequence"):
        sat_text += xml_elem_to_sat_text(sat_geometry_seq)

    return sat_text.replace("\r", "")
