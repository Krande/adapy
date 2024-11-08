from __future__ import annotations

import xml.etree.ElementTree as ET


def embed_sat_geometry(root: ET.Element) -> None:
    geom = ET.SubElement(root, "geometry")
    sat_embedded = ET.SubElement(geom, "sat_embedded", dict(encoding="base64", compression="zip", tag_name="dnvscp"))

    # Temporary placeholder text for CDATA
    sat_embedded.text = "__CDATA_PLACEHOLDER__"


def create_sesam_sat_bytes(sat_body_str: str) -> bytes:
    from datetime import datetime

    date = datetime.now()
    date_string = date.strftime("%d %a %b %d %H:%M:%S %Y")

    header = f"""2000 0 1 0
18 SESAM - gmGeometry 14 ACIS 30.0.1 NT {date_string}
1000 9.9999999999999995e-07 1e-10\n"""

    footer = "End-of-ACIS-data "

    return bytes(header + sat_body_str + footer, encoding="utf8")
