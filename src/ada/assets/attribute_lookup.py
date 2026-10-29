"""Indexed attributes of many nodes at once, for a provider reading its format into objects.

A concepts reader (``ada.assets.concepts``) may take an ``attributes`` argument: a
:class:`CollectionAttributes`, a :class:`NodeAttributeLookup` over the attribute index of the scope
the read runs in, bound to the collection of the node being read. The index is
written by whichever provider indexed the collection, so a reader can learn facts about its nodes
that its own source does not carry. A reader asks once per collection, never once per node.

A lookup that has nothing to answer (no index, no such node) answers empty; a reader then does
what it did without one.

SEVERAL PROVIDERS. A collection may be indexed by more than one provider, each under the same node
ids. A lookup reads all of them and answers one row per node (:func:`merge_provider_rows`): the
provider whose row is the most complete leads, and the others fill the keys it lacks. A node only
one provider indexed is answered from that provider alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

__all__ = [
    "CollectionAttributes",
    "IndexedNode",
    "NodeAttributeLookup",
    "NodeAttributes",
    "StaticAttributeLookup",
    "accepts_attributes",
    "merge_provider_rows",
]


def merge_provider_rows(rows: list[Mapping[str, Any]], sizes: list[int] | None = None) -> dict:
    """One node's rows from different providers as one row.

    The rows are ranked by completeness, ``sizes`` (each row's attribute count before any key
    filter; default: the keys it carries), most first, ties by provider name. Each ``attrs`` key,
    and ``parent_id``, ``full_name`` and ``kind``, is taken from the first ranked row that has it,
    so the leading row wins every key both have. ``provider`` is the leading row's, ``providers``
    every row's in rank order."""
    if not rows:
        raise ValueError("no rows to merge")
    counts = list(sizes) if sizes is not None else [len(r.get("attrs") or {}) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: (-counts[i], str(rows[i].get("provider") or "")))
    ranked = [rows[i] for i in order]
    attrs: dict[str, Any] = {}
    for row in reversed(ranked):
        attrs.update(row.get("attrs") or {})
    out = dict(ranked[0])
    out["attrs"] = attrs
    for column in ("parent_id", "full_name", "kind"):
        out[column] = next((r.get(column) for r in ranked if r.get(column)), None)
    out["providers"] = [r.get("provider") for r in ranked]
    return out


@dataclass(frozen=True)
class IndexedNode:
    node_id: str
    parent_id: str | None = None
    full_name: str | None = None
    kind: str | None = None
    attrs: Mapping[str, Any] = field(default_factory=dict)
    provider: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "IndexedNode":
        return cls(
            node_id=str(raw["node_id"]),
            parent_id=raw.get("parent_id") or None,
            full_name=raw.get("full_name"),
            kind=raw.get("kind"),
            attrs=dict(raw.get("attrs") or {}),
            provider=raw.get("provider"),
        )


@dataclass(frozen=True)
class NodeAttributes:
    """The answer to one lookup: the nodes asked for, and the ancestors asked with them."""

    indexed: bool = False
    nodes: Mapping[str, IndexedNode] = field(default_factory=dict)
    ancestors: Mapping[str, IndexedNode] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NodeAttributes":
        nodes = [IndexedNode.from_dict(n) for n in raw.get("nodes") or ()]
        above = raw.get("ancestors") or {}
        rows = above.values() if isinstance(above, Mapping) else above
        return cls(
            indexed=bool(raw.get("indexed")),
            nodes={n.node_id: n for n in nodes},
            ancestors={a.node_id: a for a in (IndexedNode.from_dict(r) for r in rows)},
        )

    def merged(self, other: "NodeAttributes") -> "NodeAttributes":
        """This answer and ``other``'s, this one's rows winning (chunks of one request)."""
        return NodeAttributes(
            indexed=self.indexed or other.indexed,
            nodes={**other.nodes, **self.nodes},
            ancestors={**other.ancestors, **self.ancestors},
        )

    def by_full_name(self) -> dict[str, IndexedNode]:
        return {n.full_name: n for n in self.nodes.values() if n.full_name}

    def node(self, node_id: str) -> IndexedNode | None:
        return self.nodes.get(node_id) or self.ancestors.get(node_id)

    def chain(self, node: IndexedNode) -> list[IndexedNode]:
        """``node``'s ancestors this answer holds, nearest first, stopping at the first gap."""
        out: list[IndexedNode] = []
        seen = {node.node_id}
        cursor = node.parent_id
        while cursor and cursor not in seen:
            seen.add(cursor)
            parent = self.node(cursor)
            if parent is None:
                break
            out.append(parent)
            cursor = parent.parent_id
        return out


@runtime_checkable
class NodeAttributeLookup(Protocol):
    def lookup(
        self,
        collection: str,
        *,
        node_ids: Iterable[str] = (),
        full_names: Iterable[str] = (),
        keys: Iterable[str] | None = None,
        ancestors: int = 0,
    ) -> NodeAttributes:
        """The nodes of ``collection`` named by id or full name, with ``attrs`` restricted to
        ``keys`` (None: all), and their ancestors up to ``ancestors`` levels above them."""
        ...


class CollectionAttributes:
    """A lookup bound to the collection of the node being read: what a concepts reader receives."""

    def __init__(self, lookup: NodeAttributeLookup, collection: str):
        self._lookup = lookup
        self.collection = collection

    def lookup(
        self,
        *,
        node_ids: Iterable[str] = (),
        full_names: Iterable[str] = (),
        keys: Iterable[str] | None = None,
        ancestors: int = 0,
    ) -> NodeAttributes:
        return self._lookup.lookup(
            self.collection, node_ids=node_ids, full_names=full_names, keys=keys, ancestors=ancestors
        )


class StaticAttributeLookup:
    """A lookup over rows held in memory: tests, and a caller that already has them."""

    def __init__(self, rows_by_collection: Mapping[str, Iterable[Mapping[str, Any]]]):
        """Rows of one node id from several providers are answered merged (:func:`merge_provider_rows`)."""
        self._rows = {}
        for c, rows in rows_by_collection.items():
            by_id: dict[str, list[Mapping[str, Any]]] = {}
            for r in rows:
                by_id.setdefault(str(r["node_id"]), []).append(r)
            self._rows[c.lower()] = {
                node_id: IndexedNode.from_dict(same[0] if len(same) == 1 else merge_provider_rows(same))
                for node_id, same in by_id.items()
            }
        self.calls: list[dict] = []

    def lookup(self, collection, *, node_ids=(), full_names=(), keys=None, ancestors=0) -> NodeAttributes:
        ids, names = set(node_ids), set(full_names)
        self.calls.append({"collection": collection, "node_ids": ids, "full_names": names})
        rows = self._rows.get(collection.lower())
        if rows is None:
            return NodeAttributes()
        want = set(keys) if keys is not None else None

        def cut(n: IndexedNode) -> IndexedNode:
            if want is None:
                return n
            return IndexedNode(
                n.node_id, n.parent_id, n.full_name, n.kind, {k: v for k, v in n.attrs.items() if k in want}, n.provider
            )

        hits = {n.node_id: cut(n) for n in rows.values() if n.node_id in ids or n.full_name in names}
        above: dict[str, IndexedNode] = {}
        for n in hits.values():
            cursor, level = n.parent_id, 0
            while cursor and level < ancestors and cursor in rows:
                if cursor not in hits:
                    above.setdefault(cursor, cut(rows[cursor]))
                cursor, level = rows[cursor].parent_id, level + 1
        return NodeAttributes(indexed=True, nodes=hits, ancestors=above)


def accepts_attributes(fn: Any) -> bool:
    """Whether ``fn`` takes an ``attributes`` keyword (by name or through ``**kwargs``)."""
    import inspect

    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "attributes" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
