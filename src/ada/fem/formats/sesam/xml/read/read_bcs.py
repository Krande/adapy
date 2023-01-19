import xml.etree.ElementTree as ET

from ada import Node, Part
from ada.fem import Bc, FemSet


def get_boundary_conditions(xml_root: ET.Element, parent: Part) -> list[Bc]:
    bcs = []
    dof_map = dict(dx=1, dy=2, dz=3, rx=4, ry=5, rz=6)
    for sp in xml_root.findall(".//support_point"):
        name = sp.attrib.get("name")
        position = sp.findall(".//position")
        if len(position) != 1:
            raise NotImplementedError()

        # Get position
        pos = position[0]
        n = Node([float(y) for x, y in pos.items()])
        parent.fem.nodes.add(n)

        # get dofs
        dofs = []
        for dof in sp.findall(".//boundary_condition"):
            if dof.attrib["constraint"] == "fixed":
                dofs.append(dof_map.get(dof.attrib["dof"]))

        fs = FemSet(f"{name}_fs", [n])
        bc = Bc(name, fem_set=fs, dofs=dofs)
        bcs.append(bc)

    return bcs
