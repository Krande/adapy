"""Relations between written elements, from what the objects carry under :data:`RELATIONS_KEY`.

Each entry is ``{relation, name, node_id?, guid?, host_name?, host_guid?}``: ``guid`` is the related
object when it is in the model, ``host_guid`` the element an opening is cut into
(``ada.assets.relation_lookup.apply_relations`` fills them). What is written:

* ``connects_to`` with the related element written: ``IfcRelConnectsPorts`` between their first
  ports when both have ports, else ``IfcRelConnectsElements``.
* ``fills_opening`` with the host written: an ``IfcOpeningElement`` voiding the host
  (``IfcRelVoidsElement``) that the element fills (``IfcRelFillsElement``), as a window fills a wall.
* ``penetrates`` with the host written and no opening of the same host: ``IfcRelConnectsElements``.
* ``flows_to`` with the next element written: an outlet ``IfcDistributionPort`` on this element and
  an inlet on the next, both at the joint (``at``) and nested on their element (``IfcRelNests``),
  joined outlet to inlet by ``IfcRelConnectsPorts`` -- how IFC chains a run head to tail. The
  ports' ``FlowDirection`` is ``SOURCE``/``SINK`` only when the provider knows the medium's
  direction (``basis: flow``); a run known only by its modelled order gets ``SOURCEANDSINK``.
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


def _fill(ifc_store: IfcStore, element, host, opening_name: str, openings: dict) -> None:
    """An opening in ``host`` that ``element`` fills. The opening has no shape of its own: the
    element is what is in it, and the host's own geometry already has the hole where it has one.
    One opening per (host, name): the pieces of one opening -- a plate and its fitting -- fill the
    same ``IfcOpeningElement`` rather than cutting the host twice."""
    f = ifc_store.f
    opening = openings.get((host.id(), opening_name))
    if opening is None:
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
        openings[(host.id(), opening_name)] = opening
    f.create_entity(
        "IfcRelFillsElement",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        RelatingOpeningElement=opening,
        RelatedBuildingElement=element,
    )


class _Ports:
    """The ports ``flows_to`` puts on written elements, one per element and joint."""

    def __init__(self, ifc_store: IfcStore):
        self.store = ifc_store
        units = getattr(getattr(ifc_store.assembly, "units", None), "value", "m")
        self.scale = 1000.0 if str(units).lower() == "mm" else 1.0
        self._made: dict[tuple, object] = {}

    def at(self, element, at, flow: str, name: str):
        point = tuple(round(float(v) * self.scale, 6) for v in at)
        key = (element.id(), point, flow)
        port = self._made.get(key)
        if port is not None:
            return port
        f = self.store.f
        placement = f.create_entity(
            "IfcLocalPlacement",
            None,
            f.createIfcAxis2Placement3D(f.createIfcCartesianPoint(point), None, None),
        )
        port = f.create_entity(
            "IfcDistributionPort",
            GlobalId=create_guid(),
            OwnerHistory=self.store.owner_history,
            Name=name,
            ObjectPlacement=placement,
            FlowDirection=flow,
        )
        nests = next((r for r in getattr(element, "IsNestedBy", None) or () if r.is_a("IfcRelNests")), None)
        if nests is None:
            f.create_entity(
                "IfcRelNests",
                GlobalId=create_guid(),
                OwnerHistory=self.store.owner_history,
                RelatingObject=element,
                RelatedObjects=[port],
            )
        else:
            nests.RelatedObjects = [*nests.RelatedObjects, port]
        self._made[key] = port
        return port


def _flow(ifc_store: IfcStore, ports: _Ports, element, target, entry: dict) -> bool:
    """``element`` flows into ``target`` at ``entry["at"]``; False when there is no joint to place."""
    at = entry.get("at")
    if not isinstance(at, (list, tuple)) or len(at) != 3:
        return False
    known = str(entry.get("basis") or "") == "flow"
    outlet = ports.at(element, at, "SOURCE" if known else "SOURCEANDSINK", "outlet")
    inlet = ports.at(target, at, "SINK" if known else "SOURCEANDSINK", "inlet")
    ifc_store.f.create_entity(
        "IfcRelConnectsPorts",
        GlobalId=create_guid(),
        OwnerHistory=ifc_store.owner_history,
        Name="flows_to",
        RelatingPort=outlet,
        RelatedPort=inlet,
    )
    return True


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
    ports = _Ports(ifc_store)
    openings: dict = {}
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
                    _fill(ifc_store, element, host, str(e.get("name") or "opening"), openings)
                    filled_hosts.add(host.id())
                elif relation == "penetrates" and target is not None and target.is_a("IfcElement"):
                    if target.id() not in filled_hosts and not _fills(entries, e):
                        _connect(ifc_store, element, target, relation)
                elif relation == "flows_to" and target is not None and target.is_a("IfcElement"):
                    if not _flow(ifc_store, ports, element, target, e):
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
