from __future__ import annotations

import pathlib
from io import StringIO
from typing import TYPE_CHECKING, Tuple, Union

import ifcopenshell
import ifcopenshell.geom
from ifcopenshell.util.element import get_psets

from ada.api.transforms import Placement
from ada.config import logger

if TYPE_CHECKING:
    from ada import Assembly, Part, Pipe


def open_ifc(ifc_file_path: Union[str, pathlib.Path, StringIO]):
    if type(ifc_file_path) is StringIO:
        return ifcopenshell.file.from_string(str(ifc_file_path.read()))
    return ifcopenshell.open(str(ifc_file_path))


def get_ifc_property_sets(ifc_elem) -> dict:
    """Returns a dictionary of {pset_id:[prop_id, prop_id...]} for an IFC object"""
    props = dict()
    for definition in ifc_elem.IsDefinedBy:
        if definition.is_a("IfcRelDefinesByProperties") is False:
            continue
        property_set = definition.RelatingPropertyDefinition
        if property_set.is_a("IfcElementQuantity"):
            continue

        pset_name = property_set.Name.split(":")[0].strip()
        props[pset_name] = dict()
        for prop in property_set.HasProperties:
            if prop.is_a() not in ("IfcPropertySingleValue", "IfcPropertyListValue"):
                continue
            if prop.is_a("IfcPropertySingleValue"):
                res = prop.NominalValue.wrappedValue if prop.NominalValue is not None else None
                props[pset_name][prop.Name] = res
            else:
                props[pset_name][prop.Name] = [x.wrappedValue for x in prop.ListValues]

    return props


def get_parent(instance):
    from ifcopenshell.util.element import get_container

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

    return get_container(instance)


def get_associated_material(ifc_elem: ifcopenshell.entity_instance):
    c = None
    for association in ifc_elem.HasAssociations:
        if association.is_a("IfcRelAssociatesMaterial") is False:
            continue
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
        raise ValueError(f"{ifc_elem=} lacks associated Material properties")

    return c


def get_beam_type(ifc_elem: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance:
    for typed_by in ifc_elem.IsTypedBy:
        if typed_by.RelatingType.is_a("IfcBeamType"):
            return typed_by.RelatingType


def get_name_from_props(props: dict) -> str | None:
    name = None
    for key, val in props.items():
        if isinstance(val, dict):
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

    logger.debug(f'Name/tag not found for ifc element "{product}". Using GlobalID as name')
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


def add_to_assembly(assembly: Assembly, obj, ifc_parent, elements2part):
    from ada import Pipe

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
        res = assembly.get_by_name(pp_name)
        if res is not None:
            add_to_parent(res, obj)
            imported = True

        if imported is False:
            for pipe in assembly.get_all_physical_objects(by_type=Pipe):
                if pipe.name == pp_name or pipe.metadata.get("original_name") == pp_name:
                    add_to_parent(pipe, obj)
                    imported = True
                    break

    if imported is False:
        logger.info(f'Unable to find parent "{pp_name}" for {type(obj)} "{obj.name}". Adding to Assembly')
        assembly.add_shape(obj)


def add_to_parent(parent: Part | Pipe, obj):
    from ada import Beam, Part, Pipe, PipeSegElbow, PipeSegStraight, Plate, Shape

    if isinstance(obj, Beam):
        parent.add_beam(obj)
    elif isinstance(obj, Plate):
        parent.add_plate(obj)
    elif issubclass(type(obj), Shape) and isinstance(parent, Part):
        parent.add_shape(obj)
    elif isinstance(obj, (PipeSegStraight, PipeSegElbow)) and isinstance(parent, Part):
        pipe = Pipe.from_segments(parent.name, [obj])
        parent.parent.add_pipe(pipe)
        parent.parent.parts.pop(parent.name)
    elif isinstance(obj, (PipeSegStraight, PipeSegElbow)) and isinstance(parent, Pipe):
        # Todo: PipeSegments should not really be resolved here.
        obj.parent = parent
        parent.segments.append(obj)
    elif isinstance(obj, Pipe):
        parent.add_pipe(obj)
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


def get_axis_polyline_points_from_product(product) -> list[tuple[float, float, float]]:
    axis_data = []
    for axis in filter(lambda x: x.RepresentationIdentifier == "Axis", product.Representation.Representations):
        if len(axis.Items) != 1:
            raise ValueError("Axis should only contain 1 item")
        for cartesian_point in axis.Items[0].Points:
            axis_data.append(cartesian_point.Coordinates)

    return axis_data


def get_ifc_body_shape_representation(product, allow_multiple=False) -> ifcopenshell.entity_instance:
    bodies = []
    for body in filter(lambda x: x.RepresentationIdentifier != "Axis", product.Representation.Representations):
        if len(body.Items) != 1:
            raise ValueError("Axis should only contain 1 item")
        bodies.append(body)

    if len(bodies) != 1:
        if allow_multiple is False:
            raise ValueError("Currently do not support multi body IFC products")
        else:
            return bodies

    return bodies[0]


def get_ifc_body(product, allow_multiple=False) -> ifcopenshell.entity_instance:
    bodies = []
    for body in filter(lambda x: x.RepresentationIdentifier != "Axis", product.Representation.Representations):
        if len(body.Items) != 1:
            raise ValueError("Axis should only contain 1 item")
        bodies.append(body.Items[0])

    if len(bodies) != 1:
        if allow_multiple is False:
            raise ValueError("Currently do not support multi body IFC products")
        else:
            return bodies

    return bodies[0]


def get_swept_area(product: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance:
    from .exceptions import UnableToConvertBoolResToBeamException

    body = get_ifc_body(product)
    if body.is_a("IfcBooleanResult"):
        raise UnableToConvertBoolResToBeamException(f"Unable to convert {product} to beam")

    return body.SweptArea
