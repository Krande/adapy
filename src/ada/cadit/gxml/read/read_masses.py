import xml.etree.ElementTree as ET

import ada
from ada import Node, Part


def get_masses(xml_root: ET.Element, parent: Part) -> None:
    for sp in xml_root.findall(".//point_mass"):
        name = sp.attrib.get("name")

        # Get position
        pos = sp.findall(".//position")[0]
        n = Node([float(y) for x, y in pos.items()])
        parent.fem.nodes.add(n)

        # Get mass
        mass_res = sp.findall(".//mass_scalar")[0]
        mass_value = float(mass_res.attrib.get("mass"))
        parent.add_mass(ada.MassPoint(name, n.p, mass=mass_value))
