"""Typed relations between published nodes, read for a model about to be written.

A provider publishes how its nodes relate beyond the tree -- ``serves_system``, ``connects_to``,
``fills_opening``, ``penetrates`` -- into core's relations index (``node-relations.ndjson.gz``). A
writer reads them through a :class:`NodeRelationLookup`, ONE batched lookup per export, for the
elements it writes, so a relation holds whatever else the export does or does not contain.

:func:`apply_relations` turns the answer into what the IFC writer writes: a system of
``ada.api.systems`` per served system, with the elements that serve it as members, and on each
element the connections and openings it has (``ada.cadit.ifc.write.write_relations``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

__all__ = [
    "FLOWS_TO",
    "NodeRelation",
    "NodeRelationLookup",
    "NodeRelations",
    "SYSTEM_TYPE_KEY",
    "StaticRelationLookup",
    "apply_relations",
    "order_by_flow",
]

#: The relation naming the next element downstream in a run.
FLOWS_TO = "flows_to"
#: System metadata key holding the ``IfcDistributionSystemEnum`` value a provider asked for.
SYSTEM_TYPE_KEY = "ifc_system_type"


@dataclass(frozen=True)
class NodeRelation:
    from_node_id: str
    relation: str
    from_full_name: str | None = None
    to_node_id: str | None = None
    to_full_name: str | None = None
    props: Mapping[str, Any] = field(default_factory=dict)
    provider: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NodeRelation":
        return cls(
            from_node_id=str(raw["from_node_id"]),
            relation=str(raw["relation"]),
            from_full_name=raw.get("from_full_name"),
            to_node_id=raw.get("to_node_id"),
            to_full_name=raw.get("to_full_name"),
            props=dict(raw.get("props") or {}),
            provider=raw.get("provider"),
        )

    @property
    def target(self) -> str:
        """The target as a name: its full name, else its node id."""
        return str(self.to_full_name or self.to_node_id or "")


@dataclass(frozen=True)
class NodeRelations:
    """One lookup's answer. ``token`` identifies the relation sets read (None: none exist)."""

    indexed: bool = False
    relations: tuple[NodeRelation, ...] = ()
    token: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NodeRelations":
        return cls(
            indexed=bool(raw.get("indexed")),
            relations=tuple(NodeRelation.from_dict(r) for r in raw.get("relations") or ()),
            token=raw.get("token"),
        )

    def merged(self, other: "NodeRelations") -> "NodeRelations":
        seen = list(self.relations)
        return NodeRelations(
            indexed=self.indexed or other.indexed,
            relations=self.relations + tuple(r for r in other.relations if r not in seen),
            token=self.token or other.token,
        )


@runtime_checkable
class NodeRelationLookup(Protocol):
    def relations(
        self, collection: str, *, node_ids: Iterable[str] = (), full_names: Iterable[str] = ()
    ) -> NodeRelations:
        """The relations FROM the nodes of ``collection`` named by id or full name."""
        ...


class StaticRelationLookup:
    """A lookup over rows held in memory: tests, and a caller that already has them."""

    def __init__(self, rows_by_collection: Mapping[str, Iterable[Mapping[str, Any]]], token: str | None = "static"):
        self._rows = {c.lower(): [NodeRelation.from_dict(r) for r in rows] for c, rows in rows_by_collection.items()}
        self._token = token
        self.calls: list[dict] = []

    def relations(self, collection, *, node_ids=(), full_names=()) -> NodeRelations:
        ids, names = set(node_ids), set(full_names)
        self.calls.append({"collection": collection, "node_ids": ids, "full_names": names})
        rows = self._rows.get(collection.lower())
        if rows is None:
            return NodeRelations()
        hits = tuple(r for r in rows if r.from_node_id in ids or (r.from_full_name and r.from_full_name in names))
        return NodeRelations(indexed=True, relations=hits, token=self._token)


def _named_objects(model: Any) -> dict[str, Any]:
    """Every part and physical object of ``model`` by name; the first of a repeated name wins."""
    out: dict[str, Any] = {}
    for part in model.get_all_parts_in_assembly(include_self=False):
        out.setdefault(str(part.name), part)
    for obj in model.get_all_physical_objects():
        out.setdefault(str(obj.name), obj)
    return out


def _system_class(kind: str | None):
    from ada.api.systems.base import SYSTEM_KINDS, System

    return SYSTEM_KINDS.get(str(kind or "").lower(), System)


def apply_relations(
    model: Any, lookup: NodeRelationLookup | None, collections: Iterable[str]
) -> tuple[int, str | None]:
    """Read the relations of ``model``'s elements, one lookup per collection, and attach them.

    * ``serves_system``: the element becomes a member of the ``ada.api.systems`` system named by
      the target, of the kind ``props.system_kind`` names (``duct``, ``piping``, ...), created on
      ``model.systems`` if the model has none of that name. ``props.system_type`` (an
      ``IfcDistributionSystemEnum`` value) overrides the kind's default type.
    * ``connects_to``, ``fills_opening``, ``penetrates``, ``flows_to``: recorded on the element for
      the IFC writer (``write_relations.RELATIONS_KEY``), with the related element when it is in
      the model; ``flows_to`` keeps its joint (``at``, ``dir``, ``basis``).

    Then siblings joined by ``flows_to`` are put in run order (:func:`order_by_flow`), which is the
    order every writer emits them in. Elements are found by name, which for a provider's objects is
    the node's full name. Returns ``(relations attached, token)``; a lookup that fails costs the
    relations, never the export.
    """
    from ada.cadit.ifc.write.write_relations import RELATIONS_KEY

    if lookup is None:
        return 0, None
    named = _named_objects(model)
    if not named:
        return 0, None
    answer = NodeRelations()
    for collection in sorted({c for c in collections if c}):
        try:
            answer = answer.merged(lookup.relations(collection, full_names=sorted(named)))
        except Exception as exc:  # noqa: BLE001 - the relations are an addition, never a requirement
            from ada.config import logger

            logger.warning("node relations: lookup failed for %s: %s", collection, exc)
    if getattr(model, "systems", None) is None:
        model.systems = []
    systems = {s.name: s for s in model.systems}
    attached = 0
    for rel in answer.relations:
        obj = named.get(str(rel.from_full_name or ""))
        if obj is None or not rel.target:
            continue
        if rel.relation == "serves_system":
            system = systems.get(rel.target)
            if system is None:
                system = _system_class(rel.props.get("system_kind"))(rel.target)
                if rel.props.get("system_type"):
                    system.metadata[SYSTEM_TYPE_KEY] = str(rel.props["system_type"]).upper()
                model.systems.append(system)
                systems[rel.target] = system
            system.add_member(obj)
        else:
            entry = {"relation": rel.relation, "name": rel.target, "node_id": rel.to_node_id}
            target = named.get(rel.target)
            if target is not None and target is not obj:
                entry["guid"] = target.guid
            if rel.relation == FLOWS_TO:
                for key in ("at", "dir", "basis"):
                    if rel.props.get(key) is not None:
                        entry[key] = rel.props[key]
            host_name = rel.props.get("host_full_name")
            if host_name:
                entry["host_name"] = str(host_name)
                host = named.get(str(host_name))
                if host is not None and host is not obj:
                    entry["host_guid"] = host.guid
            obj.metadata.setdefault(RELATIONS_KEY, []).append(entry)
        attached += 1
    order_by_flow(model)
    return attached, answer.token


def order_by_flow(model: Any) -> int:
    """Put the child parts of every part of ``model`` in run order: a child whose ``flows_to``
    names a sibling comes before it, chains are followed from their heads, and children outside any
    chain keep their place after them. Returns the number of parts reordered.

    The writers emit children in this order -- the IFC file and the STEP assembly tree alike -- so
    a run reads head to tail. A cycle or a fork keeps the first order it meets."""
    from ada.cadit.ifc.write.write_relations import RELATIONS_KEY

    reordered = 0
    for part in [model, *model.get_all_parts_in_assembly(include_self=False)]:
        children = list(part.parts.values())
        if len(children) < 2:
            continue
        by_name = {str(c.name): c for c in children}
        nxt: dict[str, str] = {}
        has_prev: set[str] = set()
        for c in children:
            for e in (c.metadata or {}).get(RELATIONS_KEY) or ():
                target = str(e.get("name") or "")
                if e.get("relation") == FLOWS_TO and target in by_name and target != str(c.name):
                    if str(c.name) not in nxt and target not in has_prev:
                        nxt[str(c.name)] = target
                        has_prev.add(target)
        if not nxt:
            continue
        ordered: list[str] = []
        placed: set[str] = set()
        for c in children:
            name = str(c.name)
            if name in has_prev or name in placed or name not in nxt:
                continue
            while name and name not in placed:
                ordered.append(name)
                placed.add(name)
                name = nxt.get(name)
        ordered += [str(c.name) for c in children if str(c.name) not in placed]
        if ordered != [str(c.name) for c in children]:
            part._parts = {name: by_name[name] for name in ordered}
            reordered += 1
    return reordered
