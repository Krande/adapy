import logging

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element

from ada.config import Settings
from ada.core.utils import Counter

name_gen = Counter(1, "IfcEl")
tol_map = dict(m=Settings.mtol, mm=Settings.mmtol)


def open_ifc(ifc_file_path):
    return ifcopenshell.open(str(ifc_file_path))


def getIfcPropertySets(ifcelem):
    """Returns a dictionary of {pset_id:[prop_id, prop_id...]} for an IFC object"""
    props = dict()
    # get psets for this pid
    for definition in ifcelem.IsDefinedBy:
        # To support IFC2X3, we need to filter our results.
        if definition.is_a("IfcRelDefinesByProperties"):
            property_set = definition.RelatingPropertyDefinition
            pset_name = property_set.Name.split(":")[0].strip()
            props[pset_name] = dict()
            if property_set.is_a("IfcElementQuantity"):
                continue
            for prop in property_set.HasProperties:
                if prop.is_a("IfcPropertySingleValue"):
                    props[pset_name][prop.Name] = prop.NominalValue.wrappedValue
            # Returning first instance of RelDefines
            # return props (Why?)
    return props


def get_parent(instance):
    if instance.is_a("IfcOpeningElement"):
        return instance.VoidsElements[0].RelatingBuildingElement
    if instance.is_a("IfcElement"):
        fills = instance.FillsVoids
        if len(fills):
            return fills[0].RelatingOpeningElement
        containments = instance.ContainedInStructure
        if len(containments):
            return containments[0].RelatingStructure
    if instance.is_a("IfcObjectDefinition"):
        decompositions = instance.Decomposes
        if len(decompositions):
            return decompositions[0].RelatingObject


def get_associated_material(ifc_elem):
    """

    :param ifc_elem:
    :return:
    """
    c = None
    for association in ifc_elem.HasAssociations:
        if association.is_a("IfcRelAssociatesMaterial"):
            material = association.RelatingMaterial
            if material.is_a("IfcMaterialProfileSet"):
                # For now, we only deal with a single profile
                c = material.MaterialProfiles[0]
            if material.is_a("IfcMaterialProfileSetUsage"):
                c = material.ForProfileSet.MaterialProfiles[0]
            if material.is_a("IfcRelAssociatesMaterial"):
                c = material.RelatingMaterial
            if material.is_a("IfcMaterial"):
                c = material
    if c is None:
        raise ValueError(f'IfcElem "{ifc_elem.Name}" lacks associated Material properties')

    return c


def get_name(ifc_elem):
    """

    :param ifc_elem:
    :return:
    """
    props = getIfcPropertySets(ifc_elem)
    product_name = ifc_elem.Name
    if hasattr(props, "NAME") and product_name is None:
        name = props["NAME"]
    else:
        name = product_name
    if name is None:
        name = next(name_gen)
    return name


def get_person(f, user_id):
    for p in f.by_type("IfcPerson"):
        if p.Identification == user_id:
            return p
    return None


def get_org(f, org_id):
    for p in f.by_type("IfcOrganization"):
        if p.Identification == org_id:
            return p
    return None


def add_to_assembly(assembly, obj, ifc_parent, elements2part):
    parent_name = ifc_parent.Name if ifc_parent.Name is not None else get_name(ifc_parent)
    imported = False
    if elements2part is not None:
        add_to_parent(assembly, obj)
        imported = True
    else:
        all_parts = assembly.get_all_parts_in_assembly()
        for p in all_parts:
            if p.name == parent_name or p.metadata.get("original_name") == parent_name:
                add_to_parent(p, obj)
                imported = True
                break

    if imported is False:
        logging.info(f'Unable to find parent "{parent_name}" for {type(obj)} "{obj.name}". Adding to Assembly')
        assembly.add_shape(obj)


def add_to_parent(parent, obj):
    from ada.concepts.primitives import Shape
    from ada.concepts.structural import Beam, Plate

    if type(obj) is Beam:
        parent.add_beam(obj)
    elif type(obj) is Plate:
        parent.add_plate(obj)
    elif issubclass(type(obj), Shape):
        parent.add_shape(obj)
    else:
        raise NotImplementedError("")
