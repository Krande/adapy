from ada.base.non_phyical_objects import Backend

from ..ifc.utils import create_guid
from .metals import CarbonSteel


class Material(Backend):
    """
    A basic material class


    :param name: Name of material
    :param mat_model: Material model. Default is ada.materials.metals.CarbonSteel
    :param mat_id: Material ID
    """

    def __init__(
        self,
        name,
        mat_model=CarbonSteel("S355"),
        mat_id=None,
        parent=None,
        metadata=None,
        units="m",
        ifc_mat=None,
        guid=None,
    ):
        super(Material, self).__init__(name, guid, metadata, units)
        self._mat_model = mat_model
        self._mat_id = mat_id
        self._parent = parent
        if ifc_mat is not None:
            props = self._import_from_ifc_mat(ifc_mat)
            self.__dict__.update(props)
        self._ifc_mat = None

    def __eq__(self, other):
        """
        Assuming uniqueness of Material Name and parent

        TODO: Make this check for same Material Model parameters

        :param other:
        :type other: Material
        :return:
        """
        # other_parent = other.__dict__['_parent']
        # other_name = other.__dict__['_name']
        # if self.name == other_name and other_parent == self.parent:
        #     return True
        # else:
        #     return False

        for key, val in self.__dict__.items():
            if "parent" in key or key == "_mat_id":
                continue
            if other.__dict__[key] != val:
                return False

        return True

    def _generate_ifc_mat(self):

        if self.parent is None:
            raise ValueError("Parent cannot be None")

        a = self.parent.get_assembly()
        f = a.ifc_file

        owner_history = a.user.to_ifc()

        ifc_mat = f.createIfcMaterial(self.name, None, "Steel")
        properties = []
        if type(self) is CarbonSteel:
            strength_grade = f.create_entity("IfcText", self.model.grade)
            properties.append(strength_grade)
        mass_density = f.create_entity("IfcMassDensityMeasure", float(self.model.rho))
        if self.model.sig_y is not None:
            yield_stress = f.create_entity("IfcPressureMeasure", float(self.model.sig_y))
            properties += [
                f.create_entity(
                    "IfcPropertySingleValue",
                    Name="YieldStress",
                    NominalValue=yield_stress,
                )
            ]
        young_modulus = f.create_entity("IfcModulusOfElasticityMeasure", float(self.model.E))
        poisson_ratio = f.create_entity("IfcPositiveRatioMeasure", float(self.model.v))
        therm_exp_coeff = f.create_entity("IfcThermalExpansionCoefficientMeasure", float(self.model.alpha))
        specific_heat = f.create_entity("IfcSpecificHeatCapacityMeasure", float(self.model.zeta))
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
            "Name": self.name,
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

    def _import_from_ifc_mat(self, ifc_mat):
        from ada.materials.metals import CarbonSteel, Metal

        mat_psets = ifc_mat.HasProperties
        scale_pascal = 1 if self.units == "mm" else 1e6
        scale_volume = 1 if self.units == "m" else 1e-9
        props = {entity.Name: entity.NominalValue[0] for entity in mat_psets[0].Properties}

        mat_props = dict(
            E=props.get("YoungModulus", 210000 * scale_pascal),
            sig_y=props.get("YieldStress", 355 * scale_pascal),
            rho=props.get("MassDensity", 7850 * scale_volume),
            v=props.get("PoissonRatio", 0.3),
            alpha=props.get("ThermalExpansionCoefficient", 1.2e-5),
            zeta=props.get("SpecificHeatCapacity", 1.15),
        )

        if "StrengthGrade" in props:
            mat_model = CarbonSteel(grade=props["StrengthGrade"], **mat_props)
        else:
            mat_model = Metal(sig_u=None, eps_p=None, sig_p=None, plasticitymodel=None, **mat_props)

        return dict(_name=ifc_mat.Name, _mat_model=mat_model)

    @property
    def id(self):
        return self._mat_id

    @id.setter
    def id(self, value):
        self._mat_id = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if value is None or any(x in value for x in [",", ".", "="]):
            raise ValueError("Material name cannot be None or contain special characters")
        self._name = value.strip()

    @property
    def model(self):
        return self._mat_model

    @model.setter
    def model(self, value):
        self._mat_model = value

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        self.model.units = value

    @property
    def ifc_mat(self):
        if self._ifc_mat is None:
            self._ifc_mat = self._generate_ifc_mat()
        return self._ifc_mat

    def __repr__(self):
        return f'Material(Name: "{self.name}" Material Model: "{self.model}'
