from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.cadit.gxml.write.write_loads import (
    add_acceleration_field_load,
    add_surface_load_plate,
    add_surface_load_polygon,
)
from ada.fem.concept.loads import LoadConceptAccelerationField, LoadConceptSurface

if TYPE_CHECKING:
    from ada import Part


def add_loadcase(
    global_elem: ET.Element,
    name: str,
    design_condition: str = "operating",
    fem_loadcase_number: int = 1,
    complex_type: str = "static",
    invalidated: bool = True,
    mesh_loads_as_mass: bool = False,
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
        mesh_loads_as_mass (bool): Whether to convert mesh loads to mass.

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
    loads = global_elem.find("loads")
    if loads is None:
        loads = ET.SubElement(global_elem, "loads")
    explicit_loads = loads.find("explicit_loads")
    if explicit_loads is None:
        explicit_loads = ET.SubElement(loads, "explicit_loads")

    ET.SubElement(
        explicit_loads,
        "dummy_mesh_loads_as_mass",
        {"loadcase_ref": name, "mesh_loads_as_mass": str(mesh_loads_as_mass).lower()},
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
    from ada import Point
    from ada.cadit.gxml.write.write_loads import add_line_load, add_point_load
    from ada.fem.concept.loads import LoadConceptLine, LoadConceptPoint

    global_elem = root.find("./model/analysis_domain/analyses/global")

    loads_concepts = part.concept_fem.loads.get_global_load_concepts()

    for lc_name, lc in loads_concepts.load_cases.items():
        lc_elem = add_loadcase(
            global_elem,
            name=lc_name,
            design_condition=lc.design_condition,
            complex_type=lc.complex_type,
            invalidated=lc.invalidated,
            fem_loadcase_number=lc.fem_loadcase_number,
            mesh_loads_as_mass=lc.mesh_loads_as_mass,
        )
        for load in lc.loads:
            abs_place = load.parent.parent.parent_fem.parent_part.placement.get_absolute_placement()
            origin = abs_place.origin
            if isinstance(load, LoadConceptLine):
                start = origin + load.start_point.copy()
                end = origin + load.end_point.copy()
                add_line_load(
                    global_elem,
                    lc_elem,
                    load.name,
                    start,
                    end,
                    load.intensity_start,
                    load.intensity_end,
                    load.system,
                )
            elif isinstance(load, LoadConceptPoint):
                position = origin + load.position.copy()
                add_point_load(global_elem, lc_elem, load.name, position, load.force, load.moment, load.system)
            elif isinstance(load, LoadConceptSurface):
                if load.plate_ref:
                    add_surface_load_plate(
                        global_elem,
                        lc_elem,
                        name=load.name,
                        plate_ref=load.plate_ref.name,
                        pressure=load.pressure,
                        side=load.side,
                        system=load.system,
                    )
                else:

                    points = [origin + Point(p) for p in load.points]
                    add_surface_load_polygon(
                        global_elem,
                        lc_elem,
                        name=load.name,
                        points=points,
                        pressure=load.pressure,
                        system=load.system,
                    )
            elif isinstance(load, LoadConceptAccelerationField):
                add_acceleration_field_load(
                    global_elem, lc_elem, load.acceleration, load.include_self_weight, load.rotational_field
                )
            else:
                raise ValueError(f"Unsupported load type: {type(load)}")

    # Add load case to combination if applicable
    for lcc_name, lcc in loads_concepts.load_case_combinations.items():
        lcc_elem = add_loadcase_combination(
            global_elem,
            name=lcc_name,
            design_condition=lcc.design_condition,
            complex_type=lcc.complex_type,
            convert_load_to_mass=lcc.convert_load_to_mass,
            global_scale_factor=lcc.global_scale_factor,
            equipments_type=lcc.equipments_type,
        )
        for lc_factored in lcc.load_cases:
            lc_elem = global_elem.find(f"./loadcases/loadcase_basic[@name='{lc_factored.load_case.name}']")
            if lc_elem is not None:
                add_loadcase_to_combination(
                    global_elem, lcc_elem, lc_elem, factor=lc_factored.factor, phase=lc_factored.phase
                )
