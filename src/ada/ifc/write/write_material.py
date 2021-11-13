from ada import Material
from ada.materials.metals import CarbonSteel

from ..utils import create_guid


def write_ifc_mat(material: Material):
    if material.parent is None:
        raise ValueError("Parent cannot be None")

    a = material.parent.get_assembly()
    f = a.ifc_file

    owner_history = a.user.to_ifc()

    ifc_mat = f.createIfcMaterial(material.name, None, "Steel")
    properties = []
    if type(material.model) is CarbonSteel:
        strength_grade = f.create_entity("IfcText", material.model.grade)
        strength_grade_prop = f.create_entity("IfcPropertySingleValue", Name="Grade", NominalValue=strength_grade)
        properties.append(strength_grade_prop)
    mass_density = f.create_entity("IfcMassDensityMeasure", float(material.model.rho))
    if material.model.sig_y is not None:
        yield_stress = f.create_entity("IfcPressureMeasure", float(material.model.sig_y))
        properties += [
            f.create_entity(
                "IfcPropertySingleValue",
                Name="YieldStress",
                NominalValue=yield_stress,
            )
        ]
    young_modulus = f.create_entity("IfcModulusOfElasticityMeasure", float(material.model.E))
    poisson_ratio = f.create_entity("IfcPositiveRatioMeasure", float(material.model.v))
    therm_exp_coeff = f.create_entity("IfcThermalExpansionCoefficientMeasure", float(material.model.alpha))
    specific_heat = f.create_entity("IfcSpecificHeatCapacityMeasure", float(material.model.zeta))
    properties += [
        f.create_entity(
            "IfcPropertySingleValue",
            Name="YoungModulus",
            NominalValue=young_modulus,
        ),
        f.create_entity(
            "IfcPropertySingleValue",
            Name="PoissonRatio",
            NominalValue=poisson_ratio,
        ),
        f.create_entity(
            "IfcPropertySingleValue",
            Name="ThermalExpansionCoefficient",
            NominalValue=therm_exp_coeff,
        ),
        f.create_entity(
            "IfcPropertySingleValue",
            Name="SpecificHeatCapacity",
            NominalValue=specific_heat,
        ),
        f.create_entity("IfcPropertySingleValue", Name="MassDensity", NominalValue=mass_density),
    ]

    atts = {
        "GlobalId": create_guid(),
        "OwnerHistory": owner_history,
        "Name": material.name,
        "HasProperties": properties,
    }

    f.create_entity("IfcPropertySet", **atts)

    f.create_entity(
        "IfcMaterialProperties",
        **{
            "Name": "MaterialMechanical",
            "Description": "A Material property description",
            "Properties": properties,
            "Material": ifc_mat,
        },
    )
    return ifc_mat
