from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import ifcopenshell

from ada import Material
from ada.config import logger

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def read_material(ifc_mat: ifcopenshell.entity_instance, ifc_store: IfcStore) -> Material:
    mi = MaterialImporter(ifc_store)
    return mi.read_material(ifc_mat)


@dataclass
class MaterialImporter:
    ifc_store: IfcStore

    def load_ifc_materials(self):
        for ifc_mat in self.ifc_store.f.by_type("IfcMaterial"):
            mat = self.ifc_store.assembly.add_material(self.read_material(ifc_mat))
            logger.info(f'Importing material "{mat}"')

    def read_material(self, ifc_mat: ifcopenshell.entity_instance) -> Material:
        from ada.materials.metals import CarbonSteel, Metal

        mat_psets = ifc_mat.HasProperties if hasattr(ifc_mat, "HasProperties") else None

        if mat_psets is None or len(mat_psets) == 0:
            logger.info(f'No material properties found for "{ifc_mat}"')
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
            units=self.ifc_store.assembly.units,
        )

        if "StrengthGrade" in props:
            mat_model = CarbonSteel(grade=props["StrengthGrade"], **mat_props)
        else:
            mat_model = Metal(sig_u=None, **mat_props)

        guid = None
        if len(ifc_mat.AssociatedTo) == 1:
            guid = ifc_mat.AssociatedTo[0].GlobalId

        return Material(
            name=ifc_mat.Name,
            mat_model=mat_model,
            ifc_store=self.ifc_store,
            units=self.ifc_store.assembly.units,
            guid=guid,
        )
