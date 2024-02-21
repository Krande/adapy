from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.core.constants import X, Y, Z

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
