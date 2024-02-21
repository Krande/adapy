from ada.api.groups import Group


def write_group(group: Group):
    from ada.core.guid import create_guid

    a = group.parent.get_assembly()
    owner_history = a.ifc_store.owner_history
    f = a.ifc_store.f

    ifc_group = f.create_entity("IfcGroup", group.guid, owner_history, group.name, group.description)

    relating_objects = []
    for m in group.members:
        relating_objects.append(a.ifc_store.get_by_guid(m.guid))

    f.create_entity(
        "IfcRelAssignsToGroup",
        create_guid(),
        owner_history,
        group.name,
        group.description,
        RelatedObjects=relating_objects,
        RelatingGroup=ifc_group,
    )
