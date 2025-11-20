from __future__ import annotations

from typing import TYPE_CHECKING

from ada import Material, Part
from ada.api.containers import Materials
from ada.config import logger
from ada.materials.metals import Metal

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET


def get_materials(xml_root, parent) -> Materials:
    all_mats = xml_root.findall(".//material")
    materials = [interpret_material(mat_el.attrib["name"], mat_el[0], parent) for mat_el in all_mats]
    return Materials(materials, parent)


def interpret_material(name, mat_prop, parent: Part):
    mat_prop_map = dict(
        isotropic_linear_material=isotropic_linear_material, isotropic_shear_material=isotropic_shear_material
    )
    mat_interpreter = mat_prop_map.get(mat_prop.tag, None)
    if mat_interpreter is None:
        raise ValueError(f"Missing property {mat_prop.tag}")

    return mat_interpreter(name, mat_prop, parent)


def isotropic_linear_material(name, mat_prop: ET.Element, parent: Part) -> Material:
    # RHO
    rho = mat_prop.attrib.get("density")
    if rho is None or rho == "":
        logger.warning(f"Missing density for material {name}. Defaulting to steel density")
        rho = 7850  # Default to steel density
    rho = float(rho)

    # DAMPING
    damping = mat_prop.attrib.get("damping")
    if damping is None or damping == "":
        logger.warning(f"Missing damping for material {name}. Defaulting to 3%")
        damping = 0.03  # Default to 3% damping

    damping = float(damping)

    model = Metal(
        sig_y=float(mat_prop.attrib["yield_stress"]),
        rho=rho,
        E=float(mat_prop.attrib["youngs_modulus"]),
        v=float(mat_prop.attrib["poissons_ratio"]),
        alpha=float(mat_prop.attrib["thermal_expansion"]),
        zeta=damping,
        sig_u=None,
        plasticitymodel=None,
    )
    return Material(name=name, mat_model=model, parent=parent)


def isotropic_shear_material(name, mat_prop, parent: Part) -> Material:
    model = Metal(
        sig_y=355e6,
        rho=float(mat_prop.attrib["density"]),
        E=float(mat_prop.attrib["youngs_modulus"]),
        v=float(mat_prop.attrib["poissons_ratio"]),
        alpha=float(mat_prop.attrib["thermal_expansion"]),
        zeta=float(mat_prop.attrib["damping"]),
        sig_u=None,
        plasticitymodel=None,
    )
    return Material(name=name, mat_model=model, parent=parent)
