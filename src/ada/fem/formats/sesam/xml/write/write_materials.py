from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Material, Part


def add_materials(root: ET.Element, part: Part):
    materials_elem = ET.Element("materials")

    # Add the new element underneath <properties>
    root.append(materials_elem)

    for material in part.materials:
        add_isotropic_material(material, materials_elem)


def add_isotropic_material(material: Material, xml_root: ET.Element):
    section_elem = ET.Element("material", {"name": material.name})
    section_props = ET.Element(
        "isotropic_linear_material",
        dict(
            yield_stress=str(material.model.sig_y),
            density=str(material.model.rho),
            youngs_modulus=str(material.model.E),
            poissons_ratio=str(material.model.v),
            thermal_expansion=str(material.model.alpha),
            damping=str(material.model.zeta),
        ),
    )

    section_elem.append(section_props)
    xml_root.append(section_elem)
