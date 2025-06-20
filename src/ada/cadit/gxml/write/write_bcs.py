from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.core.constants import X, Y, Z
from ada.fem.concept.constraints import ConstraintConceptDofType

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Part


def add_boundary_conditions(root: ET.Element, part: Part):
    dof_map = {y: x for x, y in dict(dx=1, dy=2, dz=3, rx=4, ry=5, rz=6).items()}

    all_bc_on_fem = list(part.fem.get_all_bcs())
    if len(all_bc_on_fem) > 0:
        for bc in all_bc_on_fem:
            if len(bc.fem_set.members) != 1:
                raise NotImplementedError()

            n = bc.fem_set.members[0]

            bc_stru = ET.SubElement(root, "structure")
            sup_point = ET.SubElement(bc_stru, "support_point", {"name": bc.name})
            sup_point.append(add_local_system(X, Y, Z))
            geom = ET.SubElement(sup_point, "geometry")
            ET.SubElement(geom, "position", {"x": str(n.x), "y": str(n.y), "z": str(n.z)})
            bc_con = ET.SubElement(sup_point, "boundary_conditions")
            for dof in range(1, 7):
                ftyp = "fixed" if dof in bc.dofs else "free"
                ET.SubElement(bc_con, "boundary_condition", dict(constraint=ftyp, dof=dof_map.get(dof)))
    else:
        for n in part.nodes:
            if not n.bc:
                continue

            bc_stru = ET.SubElement(root, "structure")
            sup_point = ET.SubElement(bc_stru, "support_point", {"name": n.bc.name})
            sup_point.append(add_local_system(X, Y, Z))
            geom = ET.SubElement(sup_point, "geometry")
            ET.SubElement(geom, "position", {"x": str(n.x), "y": str(n.y), "z": str(n.z)})
            bc_con = ET.SubElement(sup_point, "boundary_conditions")
            for dof in range(1, 7):
                ftyp = "fixed" if dof in n.bc.dofs else "free"
                ET.SubElement(bc_con, "boundary_condition", dict(constraint=ftyp, dof=dof_map.get(dof)))


def add_dof_constraints(parent: ET.Element, dof_constraints: list[ConstraintConceptDofType]):
    """
    Adds boundary_condition elements for all 6 DoFs to a support element.

    dof_constraints: Dict like:
        {
            "dx": {"constraint": "fixed"},
            "dy": {"constraint": "free"},
            "dz": {"constraint": "prescribed"},
            "rx": {"constraint": "spring", "stiffness": 10.0},
            ...
        }
    """
    bc_elem = ET.SubElement(parent, "boundary_conditions")
    for dof in dof_constraints:
        attribs = {"constraint": dof.constraint_type, "dof": dof.dof}
        if dof.constraint_type == "spring":
            attribs["stiffness"] = str(dof.spring_stiffness)
        ET.SubElement(bc_elem, "boundary_condition", attribs)


# Note: the syntax is based on exporting a Genie XML with the option "Export model topolpgy separatly for each concept" enabled, this makes use of line definiton instead of sat geometry
def add_support_curve(
    structures_elem: ET.Element,
    name: str,
    start_pos: tuple,
    end_pos: tuple,
    dof_constraints: list[ConstraintConceptDofType],
):
    """
    Adds a <support_curve> element with full boundary conditions using the new <line> syntax.
    """
    structure_elem = ET.SubElement(structures_elem, "structure")
    curve_elem = ET.SubElement(structure_elem, "support_curve", {"name": name})

    # Add local coordinate system
    curve_elem.append(add_local_system(X, Y, Z))

    # Geometry with <line> inside <wire>
    geom = ET.SubElement(curve_elem, "geometry")
    wire = ET.SubElement(geom, "wire")
    line = ET.SubElement(wire, "line")
    ET.SubElement(
        line, "position", {"x": str(start_pos[0]), "y": str(start_pos[1]), "z": str(start_pos[2]), "end": "1"}
    )
    ET.SubElement(line, "position", {"x": str(end_pos[0]), "y": str(end_pos[1]), "z": str(end_pos[2]), "end": "2"})

    # Line orientation
    ET.SubElement(curve_elem, "line_orientation")
    ET.SubElement(curve_elem.find("line_orientation"), "constant_local_system_line_orientation")

    # Boundary conditions
    add_dof_constraints(curve_elem, dof_constraints)


def add_support_point(
    structures_elem: ET.Element, name: str, pos: tuple, dof_constraints: list[ConstraintConceptDofType]
):
    """
    Adds a <support_point> element with full boundary conditions.
    """
    structure_elem = ET.SubElement(structures_elem, "structure")
    point_elem = ET.SubElement(structure_elem, "support_point", {"name": name})

    # Add local coordinate system
    point_elem.append(add_local_system(X, Y, Z))

    geom = ET.SubElement(point_elem, "geometry")
    ET.SubElement(geom, "position", {"x": str(pos[0]), "y": str(pos[1]), "z": str(pos[2])})

    add_dof_constraints(point_elem, dof_constraints)


def add_concept_constraints(root: ET.Element, part: Part) -> None:
    """
    Adds concept constraints to the GXML root element.
    This function is a placeholder for future implementation.
    """
    constraint_concepts = part.concept_fem.constraints.get_global_constraint_concepts()
    for pname, point in constraint_concepts.point_constraints.items():
        add_support_point(
            root,
            name=point.name,
            pos=(point.position.x, point.position.y, point.position.z),
            dof_constraints=point.dof_constraints,
        )
    for cname, curve in constraint_concepts.curve_constraints.items():
        add_support_curve(
            root,
            name=curve.name,
            start_pos=(curve.start_pos.x, curve.start_pos.y, curve.start_pos.z),
            end_pos=(curve.end_pos.x, curve.end_pos.y, curve.end_pos.z),
            dof_constraints=curve.dof_constraints,
        )
