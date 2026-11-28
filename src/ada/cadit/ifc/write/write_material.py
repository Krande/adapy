from ada import Material
from ada.materials.metals import CarbonSteel

#: The IfcMaterialProperties set a material's mechanical properties are written to.
MATERIAL_PROPERTIES_NAME = "MaterialMechanical"
MATERIAL_CATEGORY = "Steel"


def material_property_values(material: Material) -> list[tuple[str, object, str]]:
    """``(name, value, IFC value type)`` for every property a material is written with, in order.

    The one list both IFC writers state -- this module's ifcopenshell writer and the native one --
    so a reader finds the same properties whichever wrote the file."""
    out: list[tuple[str, object, str]] = []
    if type(material.model) is CarbonSteel:
        out.append(("Grade", material.model.grade, "IfcText"))
    if material.model.sig_y is not None:
        out.append(("YieldStress", float(material.model.sig_y), "IfcPressureMeasure"))
    out += [
        ("YoungModulus", float(material.model.E), "IfcModulusOfElasticityMeasure"),
        ("PoissonRatio", float(material.model.v), "IfcPositiveRatioMeasure"),
        ("ThermalExpansionCoefficient", float(material.model.alpha), "IfcThermalExpansionCoefficientMeasure"),
        ("SpecificHeatCapacity", float(material.model.zeta), "IfcSpecificHeatCapacityMeasure"),
        ("MassDensity", float(material.model.rho), "IfcMassDensityMeasure"),
    ]
    return out


def write_ifc_mat(f, material: Material):
    ifc_mat = f.create_entity("IfcMaterial", Name=material.name, Category=MATERIAL_CATEGORY)

    properties = [
        f.create_entity("IfcPropertySingleValue", Name=name, NominalValue=f.create_entity(ifc_type, value))
        for name, value, ifc_type in material_property_values(material)
    ]

    f.create_entity(
        "IfcMaterialProperties",
        Name=MATERIAL_PROPERTIES_NAME,
        Description="A Material property description",
        Properties=properties,
        Material=ifc_mat,
    )

    return ifc_mat
