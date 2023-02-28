import xml.etree.ElementTree as ET

from ada import Node, Part
from ada.fem import FemSet, Mass


def get_masses(xml_root: ET.Element, parent: Part) -> dict[str, Mass]:
    masses = dict()
    for sp in xml_root.findall(".//point_mass"):
        name = sp.attrib.get("name")

        # Get position
        pos = sp.findall(".//position")[0]
        n = Node([float(y) for x, y in pos.items()])
        parent.fem.nodes.add(n)

        # Get mass
        mass_res = sp.findall(".//mass_scalar")[0]
        mass_value = float(mass_res.attrib.get("mass"))
        fs = FemSet(f"{name}_fs", [n])
        masses[name] = Mass(name, ref=fs, mass=float(mass_value))

    return masses
