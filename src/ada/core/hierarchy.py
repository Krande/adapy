"""A generic, I/O-free index over a hierarchy of identified nodes.

The shape it indexes is deliberately minimal — an id, an optional stable key, an
optional display name, a parent id — because it is meant to serve *any* producer of a
model tree: adapy's own GLB scene hierarchy, an exporter for some external CAD system,
or anything else that can name a parent. This module knows nothing about where the
nodes came from, what a key means, or what kind of thing a node stands for. A key is an
opaque, domain-qualified string; two nodes carrying the same key are the same real-world
thing, and that is the entire semantics.

Ambiguity is a first-class answer. A producer's keys are unique only to the extent that
producer enforces it, which in practice is often *empirical* rather than structural — so
:meth:`HierarchyIndex.find` refuses to pick a winner when a key resolves to more than
one node, and :meth:`HierarchyIndex.is_ambiguous` lets a caller ask up front and degrade
gracefully instead of silently binding to the wrong node.

If a node ever needs a *type* rather than an identity, use
:class:`ada.base.ifc_types.SpatialTypes` — the existing vocabulary for "what kind of
spatial container is this" — rather than inventing a second one here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

__all__ = [
    "ROOT_SENTINEL",
    "SCHEMA_VERSION",
    "AmbiguousKeyError",
    "HierarchyIndex",
    "HierarchyNode",
]

#: What a root node's ``parent`` is, matching the GLB scene-extras hierarchy contract
#: (``id_hierarchy`` writes ``"*"`` for a node with no parent). ``None`` is accepted as
#: an equivalent spelling on input.
ROOT_SENTINEL = "*"

#: Version of the serialised form produced by :meth:`HierarchyIndex.to_dict`.
SCHEMA_VERSION = 1


class AmbiguousKeyError(KeyError):
    """A stable key resolved to more than one node.

    Raised by :meth:`HierarchyIndex.find` and the walks built on it, rather than
    returning an arbitrary one of the candidates. Use
    :meth:`HierarchyIndex.is_ambiguous` or :meth:`HierarchyIndex.find_all` to handle
    the case without an exception.
    """

    def __init__(self, key: str, node_ids: tuple[str, ...]):
        self.key = key
        self.node_ids = node_ids
        super().__init__(f"key {key!r} matches {len(node_ids)} nodes: {list(node_ids)}")


@dataclass(frozen=True)
class HierarchyNode:
    """One node of a hierarchy: an id, an optional identity, and a parent link."""

    id: str | int
    key: str | None = None
    name: str | None = None
    parent: str | int | None = None
    has_geometry: bool = False
    #: Free-form producer-supplied attributes, carried through serialisation untouched.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_root(self) -> bool:
        return self.parent is None or self.parent == ROOT_SENTINEL

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.key is not None:
            out["key"] = self.key
        if self.name is not None:
            out["name"] = self.name
        out["parent"] = self.parent
        out["has_geometry"] = self.has_geometry
        out.update(self.extra)
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> HierarchyNode:
        if "id" not in data:
            raise ValueError(f"hierarchy node is missing 'id': {data!r}")
        known = {"id", "key", "name", "parent", "has_geometry"}
        return HierarchyNode(
            id=data["id"],
            key=data.get("key"),
            name=data.get("name"),
            parent=data.get("parent"),
            has_geometry=bool(data.get("has_geometry", False)),
            extra={k: v for k, v in data.items() if k not in known},
        )


class HierarchyIndex:
    """An in-memory index over a flat node list, answering identity and ancestry.

    Pure data manipulation: it never reads a file, opens a connection or imports a
    format. Build it from whatever produced the nodes, then ask it questions::

        index = HierarchyIndex(nodes, key_domain="<domain>")
        index.find("<domain>:/SOME-ELEMENT-NAME")       # the node, or None
        index.ancestors("<domain>:/SOME-ELEMENT-NAME")  # nearest parent first, up to the root
        index.children("<domain>:/SOME-CONTAINER-NAME")
        index.is_ambiguous("<domain>:/SOME-ELEMENT-NAME")

    Ids are compared as strings, so a producer that writes ``5`` and one that writes
    ``"5"`` index identically; the original value is preserved for round-tripping.
    """

    def __init__(
        self,
        nodes: Iterable[HierarchyNode | dict[str, Any]] = (),
        *,
        key_domain: str | None = None,
        source: dict[str, Any] | None = None,
        schema_version: int = SCHEMA_VERSION,
    ):
        self.key_domain = key_domain
        #: Opaque producer-supplied provenance, carried through serialisation.
        self.source: dict[str, Any] = dict(source or {})
        self.schema_version = schema_version

        self._by_id: dict[str, HierarchyNode] = {}
        self._by_key: dict[str, list[str]] = {}
        self._children: dict[str, list[str]] = {}
        self._nodes: list[HierarchyNode] = []

        for n in nodes:
            self.add(n)

    # -- construction -------------------------------------------------------- #
    def add(self, node: HierarchyNode | dict[str, Any]) -> HierarchyNode:
        """Index one node.

        Raises on a duplicate id — a hierarchy with two nodes sharing an id cannot be
        walked, and failing loudly beats a silent overwrite.
        """
        n = node if isinstance(node, HierarchyNode) else HierarchyNode.from_dict(node)
        nid = str(n.id)
        if nid in self._by_id:
            raise ValueError(f"duplicate node id {n.id!r}")
        self._by_id[nid] = n
        self._nodes.append(n)
        if n.key is not None:
            self._by_key.setdefault(n.key, []).append(nid)
        if not n.is_root:
            self._children.setdefault(str(n.parent), []).append(nid)
        return n

    # -- lookup -------------------------------------------------------------- #
    def find(self, key: str) -> HierarchyNode | None:
        """The single node carrying ``key``, or ``None`` if no node does.

        Raises :class:`AmbiguousKeyError` if more than one does — see
        :meth:`is_ambiguous` and :meth:`find_all` for the non-raising path.
        """
        ids = self._by_key.get(key)
        if not ids:
            return None
        if len(ids) > 1:
            raise AmbiguousKeyError(key, tuple(ids))
        return self._by_id[ids[0]]

    def find_all(self, key: str) -> tuple[HierarchyNode, ...]:
        """Every node carrying ``key``, in insertion order. Empty when unknown."""
        return tuple(self._by_id[i] for i in self._by_key.get(key, ()))

    def is_ambiguous(self, key: str) -> bool:
        """Whether ``key`` resolves to more than one node. Reports; never resolves."""
        return len(self._by_key.get(key, ())) > 1

    def node_by_id(self, node_id: str | int) -> HierarchyNode | None:
        return self._by_id.get(str(node_id))

    # -- walks --------------------------------------------------------------- #
    def children(self, key: str) -> tuple[HierarchyNode, ...]:
        """Direct children of the node carrying ``key``, in insertion order."""
        node = self.find(key)
        if node is None:
            return ()
        return self.children_of_id(node.id)

    def children_of_id(self, node_id: str | int) -> tuple[HierarchyNode, ...]:
        return tuple(self._by_id[i] for i in self._children.get(str(node_id), ()))

    def ancestors(self, key: str) -> tuple[HierarchyNode, ...]:
        """The node's ancestors, nearest parent first, ending at the root.

        The walk stops at a node whose parent is the root sentinel (or ``None``), and
        also stops — rather than looping or raising — on a dangling parent id or a
        cycle, so a malformed tree degrades to a short answer instead of a hang.
        Returns ``()`` for an unknown key or for a node that is itself a root.
        """
        node = self.find(key)
        if node is None:
            return ()
        return self.ancestors_of_id(node.id)

    def ancestors_of_id(self, node_id: str | int) -> tuple[HierarchyNode, ...]:
        node = self._by_id.get(str(node_id))
        out: list[HierarchyNode] = []
        seen = {str(node_id)}
        while node is not None and not node.is_root:
            pid = str(node.parent)
            if pid in seen:
                break  # cycle
            parent = self._by_id.get(pid)
            if parent is None:
                break  # dangling parent id
            out.append(parent)
            seen.add(pid)
            node = parent
        return tuple(out)

    def roots(self) -> tuple[HierarchyNode, ...]:
        return tuple(n for n in self._nodes if n.is_root)

    # -- collection protocol ------------------------------------------------- #
    @property
    def nodes(self) -> tuple[HierarchyNode, ...]:
        return tuple(self._nodes)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._by_key)

    def ambiguous_keys(self) -> tuple[str, ...]:
        return tuple(k for k, ids in self._by_key.items() if len(ids) > 1)

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[HierarchyNode]:
        return iter(self._nodes)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __repr__(self) -> str:
        return f"HierarchyIndex(nodes={len(self._nodes)}, keys={len(self._by_key)}, domain={self.key_domain!r})"

    # -- serialisation ------------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        """The sidecar form: schema version, provenance, the node list, and a
        ``key -> id`` lookup table.

        ``key_index`` is a derived convenience for consumers that only need one lookup;
        ambiguous keys are left out of it deliberately, since it cannot represent them
        without picking a winner. ``nodes`` remains the full truth and is what
        :meth:`from_dict` rebuilds from.
        """
        out: dict[str, Any] = {"schema_version": self.schema_version}
        if self.source:
            out["source"] = dict(self.source)
        if self.key_domain is not None:
            out["key_domain"] = self.key_domain
        out["nodes"] = [n.to_dict() for n in self._nodes]
        out["key_index"] = {k: self._by_id[ids[0]].id for k, ids in self._by_key.items() if len(ids) == 1}
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> HierarchyIndex:
        return HierarchyIndex(
            (HierarchyNode.from_dict(n) for n in data.get("nodes", [])),
            key_domain=data.get("key_domain"),
            source=data.get("source"),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )
