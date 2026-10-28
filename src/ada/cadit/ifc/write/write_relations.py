"""Relations between written elements, from what the objects carry under :data:`RELATIONS_KEY`.

Each entry is ``{relation, name, node_id?, guid?, host_name?, host_guid?}``: ``guid`` is the related
object when it is in the model, ``host_guid`` the element an opening is cut into
(``ada.assets.relation_lookup.apply_relations`` fills them). What is written:

* ``connects_to`` with the related element written: ``IfcRelConnectsPorts`` between their first
  ports when both have ports, else ``IfcRelConnectsElements``.
* ``fills_opening`` with the host written: an ``IfcOpeningElement`` voiding the host
  (``IfcRelVoidsElement``) that the element fills (``IfcRelFillsElement``), as a window fills a wall.
* ``penetrates`` with the host written and no opening of the same host: ``IfcRelConnectsElements``.
* Anything whose other side is not in the file: kept in the element's :data:`PSET_NAME` property
  set, the related element's name and node id per relation, so the relation is not lost.

Systems are not written here: an element that serves a system is a member of an
``ada.api.systems`` system, written by ``write_equipment.write_ifc_systems``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore

__all__ = ["PSET_NAME", "RELATIONS_KEY", "write_relations"]

#: Object metadata key holding the element's relations.
RELATIONS_KEY = "node_relations"

#: The property set relations to elements outside the file are kept in. Not ``Pset_``: that prefix
#: is reserved for the standard's own sets, and no standard set records related elements.
PSET_NAME = "AdaRelations"


def _entity(ifc_store: IfcStore, guid: str | None):
    if not guid:
        return None
    try:
        return ifc_store.f.by_guid(guid)
    except RuntimeError:
        return None


def _ports(ent) -> list:
    out = []
    for rel in getattr(ent, "IsNestedBy", None) or ():
        out.extend(o for o in rel.RelatedObjects if o.is_a("IfcPort"))
    return out


def _connect(ifc_store: IfcStore, relating, related, name: str) -> None:
    f = ifc_store.f
    a_ports, b_ports = _ports(relating), _ports(related)
    if a_ports and b_ports:
        f.create_entity(
            "IfcRelConnectsPorts",
            GlobalId=create_guid(),
            OwnerHistory=ifc_store.owner_history,
            Name=name,
            RelatingPort=a_ports[0],
            RelatedPort=b_ports[0],
        )
        return
    f.create_entity(
        "IfcRelConnectsElements",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        Name=name,
        RelatingElement=relating,
        RelatedElement=related,
    )


def _fill(ifc_store: IfcStore, element, host, opening_name: str) -> None:
    """An opening in ``host`` that ``element`` fills. The opening has no shape of its own: the
    element is what is in it, and the host's own geometry already has the hole where it has one."""
    f = ifc_store.f
    placement = f.create_entity("IfcLocalPlacement", element.ObjectPlacement, f.createIfcAxis2Placement3D(
        f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None
    ))  # fmt: skip
    opening = f.create_entity(
        "IfcOpeningElement",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        Name=opening_name,
        ObjectPlacement=placement,
        PredefinedType="OPENING",
    )
    f.create_entity(
        "IfcRelVoidsElement",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        RelatingBuildingElement=host,
        RelatedOpeningElement=opening,
    )
    f.create_entity(
        "IfcRelFillsElement",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        RelatingOpeningElement=opening,
        RelatedBuildingElement=element,
    )


def _pset(ifc_store: IfcStore, element, values: dict[str, str]) -> None:
    f = ifc_store.f
    props = [
        f.create_entity("IfcPropertySingleValue", Name=k, NominalValue=f.create_entity("IfcLabel", v))
        for k, v in values.items()
    ]
    pset = f.create_entity(
        "IfcPropertySet", GlobalId=create_guid(), OwnerHistory=ifc_store.owner_history, Name=PSET_NAME, HasProperties=props
    )
    f.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        RelatedObjects=[element],
        RelatingPropertyDefinition=pset,
    )


def write_relations(ifc_store: IfcStore) -> int:
    """Write the relations of every written object that carries :data:`RELATIONS_KEY`. Returns the
    number of relations written as IFC relationships (the property-set ones not counted)."""
    a = ifc_store.assembly
    written = 0
    objects = [*a.get_all_parts_in_assembly(include_self=False), *a.get_all_physical_objects()]
    for obj in objects:
        entries = (obj.metadata or {}).get(RELATIONS_KEY) or []
        if not entries:
            continue
        element = _entity(ifc_store, obj.guid)
        if element is None:
            continue
        # Written once: a later sync of the same model does not write them again.
        obj.metadata.pop(RELATIONS_KEY, None)
        if not element.is_a("IfcElement"):
            # A container (a storey) takes no element relationships; its relations are kept.
            fallback = {}
            for e in entries:
                _keep(fallback, e)
            if fallback:
                _pset(ifc_store, element, fallback)
            continue
        filled_hosts = set()
        fallback: dict[str, str] = {}
        for e in entries:
            relation = str(e.get("relation") or "")
            target, host = _entity(ifc_store, e.get("guid")), _entity(ifc_store, e.get("host_guid"))
            try:
                if relation == "connects_to" and target is not None and target.is_a("IfcElement"):
                    _connect(ifc_store, element, target, relation)
                elif relation == "fills_opening" and host is not None and host.is_a("IfcElement"):
                    _fill(ifc_store, element, host, str(e.get("name") or "opening"))
                    filled_hosts.add(host.id())
                elif relation == "penetrates" and target is not None and target.is_a("IfcElement"):
                    if target.id() not in filled_hosts and not _fills(entries, e):
                        _connect(ifc_store, element, target, relation)
                else:
                    _keep(fallback, e)
                    continue
                written += 1
            except Exception as exc:  # noqa: BLE001 - one relation must not cost the file
                logger.warning("ifc-write: relation %r of %r not written: %s", relation, obj.name, exc)
                _keep(fallback, e)
        if fallback:
            _pset(ifc_store, element, fallback)
    return written


def _fills(entries: list[dict], penetrates: dict) -> bool:
    """Whether an opening relation names the same host as ``penetrates``: the voids/fills pair then
    says it already."""
    return any(
        e.get("relation") == "fills_opening" and e.get("host_guid") and e.get("host_guid") == penetrates.get("guid")
        for e in entries
    )


def _keep(values: dict[str, str], entry: dict) -> None:
    relation = str(entry.get("relation") or "related")
    for key, value in (
        (relation, entry.get("name")),
        (f"{relation}_node_id", entry.get("node_id")),
        (f"{relation}_host", entry.get("host_name")),
    ):
        if not value:
            continue
        values[key] = f"{values[key]}; {value}" if key in values and str(value) not in values[key] else str(value)
