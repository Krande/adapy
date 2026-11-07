"""GeniE point masses: a scalar mass at a position.

A point mass of any other kind (a mass matrix, rotational inertia) is left out by name: adapy's ``MassPoint`` here is
the scalar one, and reading the scalar part of a tensor would be a different mass.
"""

import xml.etree.ElementTree as ET

import ada
from ada import Node, Part
from ada.fem.formats import conversion_report

STAGE = "genie xml reader"


def get_masses(xml_root: ET.Element, parent: Part) -> None:
    rep = conversion_report.current()
    for sp in xml_root.findall(".//point_mass"):
        name = sp.attrib.get("name")
        mass_el = sp.find("mass")
        kinds = [c.tag for c in mass_el] if mass_el is not None else []
        if kinds != ["mass_scalar"]:
            rep.omitted(STAGE, "point_mass", name, "a point mass that is not one scalar mass", kinds=kinds)
            continue

        # Get position
        pos = sp.findall(".//position")[0]
        n = Node([float(pos.attrib[k]) for k in "xyz"])
        parent.fem.nodes.add(n)

        # Get mass
        mass_value = float(mass_el.find("mass_scalar").attrib.get("mass"))
        parent.add_mass(ada.MassPoint(name, n.p, mass=mass_value))
