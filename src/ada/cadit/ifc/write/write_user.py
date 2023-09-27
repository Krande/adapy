from datetime import datetime

import ifcopenshell

from ada.api.user import User
from ada.cadit.ifc.read.reader_utils import get_org, get_person


def create_owner_history_from_user(user: User, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    actor = None
    for ar in f.by_type("IfcActorRole"):
        if ar.Role == user.role.upper():
            actor = ar
            break

    if actor is None:
        actor = f.create_entity("IfcActorRole", Role=user.role.upper(), UserDefinedRole=None, Description=None)

    user_props = dict(
        Identification=user.user_id,
        FamilyName=user.family_name,
        GivenName=user.given_name,
        MiddleNames=user.middle_names,
        PrefixTitles=user.prefix_titles,
        SuffixTitles=user.suffix_titles,
    )

    person = get_person(f, user.user_id)
    if person is None:
        person = f.create_entity("IfcPerson", **user_props, Roles=(actor,))

    organization = get_org(f, user.org_id)
    if organization is None:
        organization = f.create_entity(
            "IfcOrganization",
            Identification=user.org_id,
            Name=user.org_name,
            Description=user.org_description,
        )

    p_o = None
    for po in f.by_type("IfcPersonAndOrganization"):
        if po.TheOrganization != organization:
            continue
        p_o = po
        break

    if p_o is None:
        p_o = f.create_entity("IfcPersonAndOrganization", person, organization)

    app_name = "ADA"
    application = None
    for app in f.by_type("IfcApplication"):
        if app.ApplicationFullName != app_name:
            continue
        application = app
        break

    if application is None:
        application = f.create_entity("IfcApplication", organization, "XXX", "ADA", "ADA")

    timestamp = int(datetime.now().timestamp())

    owner_history = None
    for oh in f.by_type("IfcOwnerHistory"):
        if oh.OwningUser != p_o:
            continue
        if oh.OwningApplication != application:
            continue
        oh.LastModifiedDate = timestamp
        owner_history = oh
        break

    if owner_history is None:
        owner_history = f.create_entity(
            "IfcOwnerHistory",
            OwningUser=p_o,
            OwningApplication=application,
            State="READWRITE",
            ChangeAction=None,
            LastModifiedDate=None,
            LastModifyingUser=p_o,
            LastModifyingApplication=application,
            CreationDate=timestamp,
        )

    return owner_history
