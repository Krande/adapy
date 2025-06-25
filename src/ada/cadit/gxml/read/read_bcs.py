import xml.etree.ElementTree as ET

import ada
from ada import Node, Part, logger


def get_boundary_conditions(xml_root: ET.Element, parent: Part) -> None:
    for sp in xml_root.findall(".//support_point"):
        name = sp.attrib.get("name")
        position = sp.findall(".//position")
        if len(position) != 1:
            raise NotImplementedError()

        # Get position
        pos = position[0]
        n = Node([float(y) for x, y in pos.items()])

        # get dofs
        constraints = []
        for dof in sp.findall(".//boundary_condition"):
            constraint = ada.ConstraintConceptDofType(dof=dof.attrib["dof"], constraint_type=dof.attrib["constraint"])
            if constraint.constraint_type == "spring":
                logger.warning("Spring constraints are not yet supported")
            constraints.append(constraint)
        parent.concept_fem.constraints.add_point_constraint(ada.ConstraintConceptPoint(name, n.p, constraints))
