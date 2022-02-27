import logging
from typing import TYPE_CHECKING

from ada import Assembly, Material

if TYPE_CHECKING:
    from ..concepts import IfcRef


def read_material(ifc_mat, ifc_ref: "IfcRef", assembly: "Assembly") -> Material:
    from ada.materials.metals import CarbonSteel, Metal

    mat_psets = ifc_mat.HasProperties if hasattr(ifc_mat, "HasProperties") else None

    if mat_psets is None or len(mat_psets) == 0:
        logging.info(f'No material properties found for "{ifc_mat}"')
        return Material(ifc_mat.Name)

    props = {}
    for entity in mat_psets[0].Properties:
        if entity.is_a("IfcPropertySingleValue"):
            props[entity.Name] = entity.NominalValue[0]

    mat_props = dict(
        E=props.get("YoungModulus", 210000e6),
        sig_y=props.get("YieldStress", 355e6),
        rho=props.get("MassDensity", 7850),
        v=props.get("PoissonRatio", 0.3),
        alpha=props.get("ThermalExpansionCoefficient", 1.2e-5),
        zeta=props.get("SpecificHeatCapacity", 1.15),
        units=assembly.units,
    )

    if "StrengthGrade" in props:
        mat_model = CarbonSteel(grade=props["StrengthGrade"], **mat_props)
    else:
        mat_model = Metal(sig_u=None, **mat_props)

    return Material(name=ifc_mat.Name, mat_model=mat_model, ifc_ref=ifc_ref, units=assembly.units)


def read_ifc_materials(f, a: Assembly, ifc_ref: "IfcRef"):
    for ifc_mat in f.by_type("IfcMaterial"):
        mat = a.add_material(read_material(ifc_mat, ifc_ref, a))
        logging.info(f'Importing material "{mat}"')
