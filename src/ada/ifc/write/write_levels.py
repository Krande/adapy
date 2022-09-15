from typing import TYPE_CHECKING

from ada.ifc.utils import (
    add_multiple_props_to_elem,
    create_guid,
    create_local_placement,
    create_property_set,
)

if TYPE_CHECKING:
    from ada import Assembly, Part


def write_ifc_assembly(assembly: "Assembly"):

    f = assembly.ifc_file
    owner_history = assembly.user.to_ifc()
    site_placement = create_local_placement(f)
    site = f.create_entity(
        "IfcSite",
        assembly.guid,
        owner_history,
        assembly.name,
        None,
        None,
        site_placement,
        None,
        None,
        "ELEMENT",
        None,
        None,
        None,
        None,
        None,
    )
    f.create_entity(
        "IfcRelAggregates",
        create_guid(),
        owner_history,
        "Project Container",
        None,
        f.by_type("IfcProject")[0],
        [site],
    )

    props = create_property_set("Properties", f, assembly.metadata, owner_history)
    f.create_entity(
        "IfcRelDefinesByProperties",
        create_guid(),
        owner_history,
        "Properties",
        None,
        [site],
        props,
    )

    return site


def write_ifc_part(part: "Part"):
    if part.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    a = part.get_assembly()
    f = a.ifc_file

    owner_history = a.user.to_ifc()

    itype = part.metadata["ifctype"]
    parent = part.parent.get_ifc_elem()
    placement = create_local_placement(
        f,
        origin=part.placement.origin,
        loc_x=part.placement.xdir,
        loc_z=part.placement.zdir,
        relative_to=parent.ObjectPlacement,
    )
    type_map = dict(building="IfcBuilding", space="IfcSpace", spatial="IfcSpatialZone", storey="IfcBuildingStorey")

    if itype not in type_map.keys() and itype not in type_map.values():
        raise ValueError(f'Currently not supported "{itype}"')

    ifc_type = type_map[itype] if itype not in type_map.values() else itype

    props = dict(
        GlobalId=part.guid,
        OwnerHistory=owner_history,
        Name=part.name,
        Description=part.metadata.get("Description", None),
        ObjectType=None,
        ObjectPlacement=placement,
        Representation=None,
        LongName=part.metadata.get("LongName", None),
    )

    if ifc_type not in ["IfcSpatialZone"]:
        props["CompositionType"] = part.metadata.get("CompositionType", "ELEMENT")

    if ifc_type == "IfcBuildingStorey":
        props["Elevation"] = float(part.placement.origin[2])

    ifc_elem = f.create_entity(ifc_type, **props)

    existing_rel_agg = False
    for rel_agg in f.by_type("IfcRelAggregates"):
        if rel_agg.RelatingObject == parent:
            rel_agg.RelatedObjects = tuple([*rel_agg.RelatedObjects, ifc_elem])
            existing_rel_agg = True
            break

    if existing_rel_agg is False:
        f.create_entity(
            "IfcRelAggregates",
            GlobalId=create_guid(),
            OwnerHistory=owner_history,
            Name="Site Container",
            Description=None,
            RelatingObject=parent,
            RelatedObjects=[ifc_elem],
        )

    if part.ifc_options.export_props is True:
        add_multiple_props_to_elem(part.metadata.get("props", dict()), ifc_elem, f, owner_history)

    return ifc_elem
