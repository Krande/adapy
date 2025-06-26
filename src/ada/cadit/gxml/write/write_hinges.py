import xml.etree.ElementTree as ET

import ada


def add_hinges(props_elem: ET.Element, part: ada.Part) -> None:
    hinges_elem = props_elem.find("./hinges")
    if hinges_elem is None:
        hinges_elem = ET.SubElement(props_elem, "hinges")

    unique_hinges: set[ada.BeamHinge] = set()

    for bm in part.get_all_physical_objects(by_type=ada.Beam):
        if bm.hinge1 is not None and bm.hinge1 not in unique_hinges:
            unique_hinges.add(bm.hinge1)
        if bm.hinge2 is not None and bm.hinge2 not in unique_hinges:
            unique_hinges.add(bm.hinge2)

    for hinge in unique_hinges:
        hinge_elem = ET.SubElement(hinges_elem, "hinge", {"name": hinge.name})
        flexible_hinge = ET.SubElement(hinge_elem, "flexible_hinge", {"coordinate_system_type": "local"})
        stiffness_elem = ET.SubElement(flexible_hinge, "stiffness")
        for dof in hinge.dofs:
            attribs = {"constraint": dof.constraint_type, "dof": dof.dof}
            if dof.constraint_type == "spring":
                attribs["stiffness"] = str(dof.spring_stiffness)
            ET.SubElement(stiffness_elem, "boundary_condition", attribs)
