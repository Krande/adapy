from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Part


def add_loadcase(
    global_elem: ET.Element,
    name: str,
    design_condition: str = "operating",
    fem_loadcase_number: int = 1,
    complex_type: str = "static",
    invalidated: bool = True,
) -> ET.Element:
    """
    Adds a <loadcase_basic> under <global><loadcases>.

    Args:
        global_elem (ET.Element): The <global> element in the XML tree.
        name (str): Load case name (e.g., "LC1").
        design_condition (str): Design condition (e.g., "operating").
        fem_loadcase_number (int): FEM load case number.
        complex_type (str): Analysis type (e.g., "static").
        invalidated (bool): Whether the load case is marked invalidated.

    Returns:
        ET.Element: The created <loadcase_basic> element.
    """
    # Update active_loadcase if not already set
    if "active_loadcase" not in global_elem.attrib:
        global_elem.attrib["active_loadcase"] = name
    if "active" not in global_elem.attrib:
        global_elem.attrib["active"] = "true"

    # Get or create <loadcases> element
    loadcases_elem = global_elem.find("loadcases")
    if loadcases_elem is None:
        loadcases_elem = ET.SubElement(global_elem, "loadcases")

    # Add <loadcase_basic>
    loadcase_elem = ET.SubElement(
        loadcases_elem,
        "loadcase_basic",
        {
            "name": name,
            "design_condition": design_condition,
            "fem_loadcase_number": str(fem_loadcase_number),
            "complex_type": complex_type,
            "invalidated": str(invalidated).lower(),
        },
    )

    return loadcase_elem


def add_loadcase_combination(
    global_elem: ET.Element,
    name: str,
    design_condition: str = "operating",
    complex_type: str = "static",
    convert_load_to_mass: bool = False,
    global_scale_factor: float = 1.0,
    equipments_type: str = "line_load",
) -> ET.Element:
    """
    Adds a <loadcase_combination> element to the <loadcases> block under <global>.

    Args:
        global_elem (ET.Element): The <global> element.
        name (str): Name of the loadcase combination (e.g., "LCC2").
        design_condition (str): Design condition (default "operating").
        complex_type (str): Complex type (default "static").
        convert_load_to_mass (bool): Whether to convert loads to mass.
        global_scale_factor (float): Global scale factor.
        equipments_type (str): Type of equipment representation ("line_load", "point_load", etc.).

    Returns:
        ET.Element: The created <loadcase_combination> element.
    """
    loadcases_elem = global_elem.find("loadcases")
    if loadcases_elem is None:
        loadcases_elem = ET.SubElement(global_elem, "loadcases")

    attribs = {
        "name": name,
        "design_condition": design_condition,
        "complex_type": complex_type,
        "convert_load_to_mass": str(convert_load_to_mass).lower(),
        "global_scale_factor": str(global_scale_factor),
    }

    lcc_elem = ET.SubElement(loadcases_elem, "loadcase_combination", attribs)
    ET.SubElement(lcc_elem, "equipments", {"representation_type": equipments_type})
    return lcc_elem


def add_loadcase_to_combination(global_elem, lcc_elem, lc_elem, factor=1.0, phase=0):
    """
    Adds a load case to a load case combination and registers it in the <combinations> block.

    Parameters:
        global_elem (Element): The <global> element.
        lcc_elem (Element): The <loadcase_combination> element where the loadcase will be added.
        lc_elem (Element): The <loadcase_basic> element to reference.
        factor (float): Scaling factor for this load case.
        phase (int): Phase number.
    """
    loadcase_ref = lc_elem.attrib["name"]
    combination_name = lcc_elem.attrib["name"]

    # Add inside <loadcase_combination>
    ET.SubElement(lcc_elem, "loadcase", {"loadcase_ref": loadcase_ref, "factor": str(factor), "phase": str(phase)})

    # Add to <combinations> block under <global>
    combinations_elem = global_elem.find("combinations")
    if combinations_elem is None:
        combinations_elem = ET.SubElement(global_elem, "combinations")

    # Find or create <combination> element for this combination
    combination_elem = None
    for combo in combinations_elem.findall("combination"):
        if combo.attrib.get("combination_ref") == combination_name:
            combination_elem = combo
            break

    if combination_elem is None:
        combination_elem = ET.SubElement(combinations_elem, "combination", {"combination_ref": combination_name})

    # Ensure <loadcases> child exists
    loadcases_elem = combination_elem.find("loadcases")
    if loadcases_elem is None:
        loadcases_elem = ET.SubElement(combination_elem, "loadcases")

    # Add the referenced <loadcase>
    ET.SubElement(
        loadcases_elem, "loadcase", {"loadcase_ref": loadcase_ref, "factor": str(factor), "phase": str(phase)}
    )


def add_loads(root: ET.Element, part: Part) -> None:
    from ada.cadit.gxml.write.write_loads import add_line_load, add_point_load
    from ada.fem.loads.concept_loads import LoadConceptLine, LoadConceptPoint

    global_elem = root.find("./model/analysis_domain/analyses/global")

    loads_concepts = part.load_concepts.get_global_load_concepts()

    for lc_name, lc in loads_concepts.load_cases.items():
        lc_elem = add_loadcase(
            global_elem,
            name=lc_name,
            design_condition=lc.design_condition,
            complex_type=lc.complex_type,
            invalidated=lc.invalidated,
        )
        for load in lc.loads:
            if isinstance(load, LoadConceptLine):
                # Handle line loads
                add_line_load(
                    global_elem,
                    lc_elem,
                    load.name,
                    load.start_point,
                    load.end_point,
                    load.intensity_start,
                    load.intensity_end,
                    load.system,
                )
            elif isinstance(load, LoadConceptPoint):
                # Handle point loads
                add_point_load(global_elem, lc_elem, load.name, load.point_ref, load.intensity, load.system)
            else:
                raise ValueError(f"Unsupported load type: {type(load)}")
