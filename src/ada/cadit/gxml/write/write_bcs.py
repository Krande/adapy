from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.core.constants import X, Y, Z
from ada.fem.concept.constraints import ConstraintConceptDofType

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import BeamHingeDofType, Part


def add_fem_boundary_conditions(root: ET.Element, part: Part):
    dof_map = {y: x for x, y in dict(dx=1, dy=2, dz=3, rx=4, ry=5, rz=6).items()}

    all_bc_on_fem = list(part.fem.get_all_bcs())
    if len(all_bc_on_fem) > 0:
        for bc in all_bc_on_fem:
            if len(bc.fem_set.members) != 1:
                raise NotImplementedError()

            abs_place = bc.parent.parent.placement.get_absolute_placement()
            origin = abs_place.origin
            p = origin + bc.fem_set.members[0].p.copy()

            bc_stru = ET.SubElement(root, "structure")
            sup_point = ET.SubElement(bc_stru, "support_point", {"name": bc.name})
            sup_point.append(add_local_system(X, Y, Z))
            geom = ET.SubElement(sup_point, "geometry")
            ET.SubElement(geom, "position", {"x": str(p.x), "y": str(p.y), "z": str(p.z)})
            bc_con = ET.SubElement(sup_point, "boundary_conditions")
            for dof in range(1, 7):
                ftyp = "fixed" if dof in bc.dofs else "free"
                ET.SubElement(bc_con, "boundary_condition", dict(constraint=ftyp, dof=dof_map.get(dof)))


def add_dof_constraints(parent: ET.Element, dof_constraints: list[ConstraintConceptDofType | BeamHingeDofType]):
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


def add_support_rigid_link(
    structures_elem: ET.Element,
    name: str,
    position: tuple,
    lower_corner: tuple,
    upper_corner: tuple,
    dof_constraints: list[ConstraintConceptDofType],
    include_all_edges: bool = True,
    rotation_dependent: bool = True,
):
    """
    Adds a <support_rigid_link> element with boundary conditions and footprint box region.
    """
    structure_elem = ET.SubElement(structures_elem, "structure")
    rigid_link_elem = ET.SubElement(
        structure_elem,
        "support_rigid_link",
        {
            "name": name,
            "include_all_edges": str(include_all_edges).lower(),
            "rotation_dependent": str(rotation_dependent).lower(),
        },
    )

    # Add local coordinate system
    rigid_link_elem.append(add_local_system(X, Y, Z))

    # Add position
    ET.SubElement(rigid_link_elem, "position", {"x": str(position[0]), "y": str(position[1]), "z": str(position[2])})

    # Add boundary conditions
    add_dof_constraints(rigid_link_elem, dof_constraints)

    # Add region with footprint_box
    region_elem = ET.SubElement(rigid_link_elem, "region")
    footprint_box_elem = ET.SubElement(region_elem, "footprint_box")

    # Add corners
    ET.SubElement(
        footprint_box_elem,
        "lower_corner",
        {"x": str(lower_corner[0]), "y": str(lower_corner[1]), "z": str(lower_corner[2])},
    )
    ET.SubElement(
        footprint_box_elem,
        "upper_corner",
        {"x": str(upper_corner[0]), "y": str(upper_corner[1]), "z": str(upper_corner[2])},
    )

    # Add local system for footprint_box
    footprint_box_elem.append(add_local_system(X, Y, Z))

    # Add local system origin
    ET.SubElement(
        footprint_box_elem, "local_system_origin", {"x": str(position[0]), "y": str(position[1]), "z": str(position[2])}
    )


def add_concept_constraints(root: ET.Element, part: Part) -> None:
    """
    Adds concept constraints to the GXML root element.
    This function is a placeholder for future implementation.
    """
    constraint_concepts = part.concept_fem.constraints.get_global_constraint_concepts()

    for pname, point in constraint_concepts.point_constraints.items():
        abs_place = point.parent.parent_fem.parent_part.placement.get_absolute_placement()
        origin = abs_place.origin
        p = origin + point.position.copy()
        add_support_point(
            root,
            name=point.name,
            pos=(p.x, p.y, p.z),
            dof_constraints=point.dof_constraints,
        )
    for cname, curve in constraint_concepts.curve_constraints.items():
        abs_place = curve.parent.parent_fem.parent_part.placement.get_absolute_placement()
        origin = abs_place.origin
        pt1 = origin + curve.start_pos.copy()
        pt2 = origin + curve.end_pos.copy()
        add_support_curve(
            root,
            name=curve.name,
            start_pos=tuple(pt1),
            end_pos=tuple(pt2),
            dof_constraints=curve.dof_constraints,
        )

    for rigid_name, rigid_link in constraint_concepts.rigid_links.items():
        abs_place = rigid_link.parent.parent_fem.parent_part.placement.get_absolute_placement()
        origin = abs_place.origin

        # Get master point position
        master_pos = origin + rigid_link.master_point.copy()

        # Default bounds - you may need to implement proper bounds extraction
        lower_corner = origin + rigid_link.influence_region.lower_corner.copy()
        upper_corner = origin + rigid_link.influence_region.upper_corner.copy()

        add_support_rigid_link(
            root,
            name=rigid_link.name,
            position=tuple(master_pos),
            lower_corner=tuple(lower_corner),
            upper_corner=tuple(upper_corner),
            dof_constraints=rigid_link.dof_constraints,
        )
