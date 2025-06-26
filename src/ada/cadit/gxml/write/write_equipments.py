from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.spatial.equipment import EquipRepr
from ada.cadit.gxml.write.write_utils import add_local_system
from ada.config import logger

if TYPE_CHECKING:
    from ada import Part


def add_equipments(root: ET.Element, part: Part):
    from ada import Equipment, LoadConceptCase

    global_elem = root.find("./model/analysis_domain/analyses/global")
    # Ensure <loads>/<explicit_loads> structure
    loads_elem = global_elem.find("loads")
    if loads_elem is None:
        loads_elem = ET.SubElement(global_elem, "loads")

    equipment_concepts = root.find(".//equipment_concepts")
    equip = ET.SubElement(equipment_concepts, "equipments")
    equip_loads = ET.SubElement(loads_elem, "equipment_loads")
    lc_refs = {}

    for p in part.get_all_parts_in_assembly(include_self=True):
        # For now we rely on dict data EQUIP_DATA for equipments. This is only temporary
        if not isinstance(p, Equipment):
            continue
        if p.eq_repr == EquipRepr.AS_IS:
            continue

        # Equipment Concept
        prism = ET.SubElement(equip, "prism_shape", {"name": p.name, "mass": str(p.mass)})
        ET.SubElement(prism, "cog", {"x": str(p.cog[0]), "y": str(p.cog[1]), "z": str(p.cog[2])})
        ET.SubElement(prism, "dimensions", {"x": str(p.lx), "y": str(p.ly), "z": str(p.lz)})
        fp_elem = ET.SubElement(prism, "footprint", {"x": str(p.lx), "y": str(p.ly), "z": str(p.lz)})
        for x1, y1, x2, y2 in p.footprint:
            ET.SubElement(fp_elem, "polygon", {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)})

        # Equipment Load
        origin = p.placement.get_absolute_placement().origin + p.origin.copy()
        lc_ref = p.load_case_ref.name if isinstance(p.load_case_ref, LoadConceptCase) else p.load_case_ref
        if lc_ref is None:
            logger.warning(f"No load case reference found for equipment {p.name}")
            lc_ref = ""

        placed_eq = ET.SubElement(
            equip_loads,
            "placed_shape",
            {"loadcase_ref": lc_ref, "equipment_ref": p.name, "moment_equilibrium": str(p.moment_equilibrium).lower()},
        )
        ET.SubElement(placed_eq, "origo", {"x": str(origin[0]), "y": str(origin[1]), "z": str(origin[2])})
        placed_eq.append(add_local_system())
        existing_lc_ref = lc_refs.get(lc_ref, None)
        if existing_lc_ref is None:
            ET.SubElement(
                equip_loads, "equipment_ref", {"loadcase_ref": lc_ref, "representation_type": p.eq_repr.value.lower()}
            )
            lc_refs[lc_ref] = p.eq_repr
        else:
            if existing_lc_ref != p.eq_repr:
                logger.warning(
                    f'Load Case "{lc_ref}" has multiple equipments with different representations. '
                    "The first one will be used."
                )
