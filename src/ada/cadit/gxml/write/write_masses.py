from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.spatial.equipment import Equipment, EquipRepr
from ada.core.constants import X, Y, Z

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Part


def add_masses(root: ET.Element, part: Part):
    all_mass_on_fem = list(part.fem.get_all_masses())
    if len(all_mass_on_fem) > 0:
        for mass in all_mass_on_fem:
            if len(mass.fem_set.members) != 1:
                raise NotImplementedError()

            n = mass.fem_set.members[0]
            abs_place = mass.parent.parent.placement.get_absolute_placement()
            origin = abs_place.origin
            pt = origin + n.p.copy()
            bc_stru = ET.SubElement(root, "structure")
            sup_point = ET.SubElement(bc_stru, "point_mass", {"name": mass.name})
            sup_point.append(add_local_system(X, Y, Z))
            geom = ET.SubElement(sup_point, "geometry")
            ET.SubElement(geom, "position", {"x": str(pt.x), "y": str(pt.y), "z": str(pt.z)})
            bc_con = ET.SubElement(sup_point, "mass")
            ET.SubElement(bc_con, "mass_scalar", dict(mass=str(mass.mass)))
    else:
        for p in part.get_all_subparts(True):
            if isinstance(p, Equipment) and p.eq_repr != EquipRepr.AS_IS:
                continue
            for mass in p.masses:
                abs_place = p.placement.get_absolute_placement()
                origin = abs_place.origin
                pt = origin + mass.cog.copy()
                bc_stru = ET.SubElement(root, "structure")
                sup_point = ET.SubElement(bc_stru, "point_mass", {"name": mass.name})
                sup_point.append(add_local_system(X, Y, Z))
                geom = ET.SubElement(sup_point, "geometry")
                ET.SubElement(geom, "position", {"x": str(pt[0]), "y": str(pt[1]), "z": str(pt[2])})
                bc_con = ET.SubElement(sup_point, "mass")
                ET.SubElement(bc_con, "mass_scalar", dict(mass=str(mass.mass)))
