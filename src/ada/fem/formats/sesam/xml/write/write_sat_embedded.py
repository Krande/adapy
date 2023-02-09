from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.sat.utils import create_sat_from_beams

if TYPE_CHECKING:
    from ada import Part


def embed_sat_geometry(root: ET.Element, part: Part) -> dict:
    geom = ET.SubElement(root, "geometry")
    sat_embedded = ET.SubElement(geom, "sat_embedded", dict(encoding="base64", compression="zip", tag_name="dnvscp"))

    # Encode and compress the string
    sat_map = dict()
    beams_sat = create_sat_from_beams(part)
    sat_map.update(beams_sat.sat_map)

    data = create_sesam_sat_bytes(beams_sat.sat_text)

    encoded_data = base64.b64encode(data).decode()

    # Add the encoded data to the element using CDATA
    sat_embedded.text = "<![CDATA[" + encoded_data + "]]>"

    return sat_map


def create_sesam_sat_bytes(sat_body_str: str) -> bytes:
    from datetime import datetime

    date = datetime.now()
    date_string = date.strftime("%d %a %b %d %H:%M:%S %Y")

    header = f"""2000 0 1 0
18 SESAM - gmGeometry 14 ACIS 30.0.1 NT {date_string}
1000 9.9999999999999995e-07 1e-10\n"""

    footer = "End-of-ACIS-data "

    return bytes(header + sat_body_str + footer, encoding="utf8")
