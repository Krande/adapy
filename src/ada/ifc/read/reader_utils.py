import logging
import pathlib
from io import StringIO
from typing import Tuple, Union

import ifcopenshell
import ifcopenshell.geom
from ifcopenshell.util.element import get_psets

from ada.concepts.transforms import Placement
from ada.config import Settings

tol_map = dict(m=Settings.mtol, mm=Settings.mmtol)


def open_ifc(ifc_file_path: Union[str, pathlib.Path, StringIO]):
    if type(ifc_file_path) is StringIO:
        return ifcopenshell.file.from_string(str(ifc_file_path.read()))
    return ifcopenshell.open(str(ifc_file_path))


def getIfcPropertySets(ifcelem):
    """Returns a dictionary of {pset_id:[prop_id, prop_id...]} for an IFC object"""
    props = dict()
    for definition in ifcelem.IsDefinedBy:
        if definition.is_a("IfcRelDefinesByProperties") is False:
            continue
        property_set = definition.RelatingPropertyDefinition
        if property_set.is_a("IfcElementQuantity"):
            continue

        pset_name = property_set.Name.split(":")[0].strip()
        props[pset_name] = dict()
        for prop in property_set.HasProperties:
            if prop.is_a("IfcPropertySingleValue") is False:
                continue

            res = prop.NominalValue.wrappedValue
            props[pset_name][prop.Name] = res

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


def get_name_from_props(props: dict) -> Union[str, None]:
    name = None
    for key, val in props.items():
        if type(val) is dict:
            name = get_name_from_props(val)
            if name is not None:
                break
        else:
            if key.lower() == "name":
                name = val
                break
    return name


def resolve_name(props, product):
    if product.Name is not None:
        return product.Name

    if hasattr(product, "Tag"):
        if product.Tag is not None:
            return product.Tag

    # This procedure is just to handle reading badly created ifc files with little or no related names
    name = get_name_from_props(props)
    if name is not None:
        return name

    logging.debug(f'Name/tag not found for ifc element "{product}". Using GlobalID as name')
    return product.GlobalId


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
    pp_name = ifc_parent.Name
    if pp_name is None:
        pp_name = resolve_name(get_psets(ifc_parent), ifc_parent)
        if pp_name is None:
            raise ValueError(f'Name of ifc element "{ifc_parent}" is None')

    imported = False
    if elements2part is not None:
        add_to_parent(assembly, obj)
        imported = True
    else:
        all_parts = assembly.get_all_parts_in_assembly()
        for p in all_parts:
            if p.name == pp_name or p.metadata.get("original_name") == pp_name:
                add_to_parent(p, obj)
                imported = True
                break

    if imported is False:
        logging.info(f'Unable to find parent "{pp_name}" for {type(obj)} "{obj.name}". Adding to Assembly')
        assembly.add_shape(obj)


def add_to_parent(parent, obj):
    from ada import Beam, Plate, Shape

    if type(obj) is Beam:
        parent.add_beam(obj)
    elif type(obj) is Plate:
        parent.add_plate(obj)
    elif issubclass(type(obj), Shape):
        parent.add_shape(obj)
    else:
        raise NotImplementedError("")


def get_point(cartesian_point) -> Tuple[float, float, float]:
    return cartesian_point.Coordinates


def get_direction(ifc_direction) -> Tuple[float, float, float]:
    return ifc_direction.DirectionRatios


def get_placement(ifc_position) -> Placement:
    origin = get_point(ifc_position.Location)
    xdir = get_direction(ifc_position.RefDirection)
    zdir = get_direction(ifc_position.Axis)

    return Placement(origin, xdir=xdir, zdir=zdir)
