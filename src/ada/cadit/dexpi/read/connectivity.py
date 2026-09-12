"""Every lookup over a document's connectivity graph, built once.

``DexpiDocument.connections`` is a flat edge list, and everything that reads it wants an answer
keyed some other way: the edges a segment owns (the importer's endpoints, the writers' grouping,
the merge writer's boundary check), the edges touching an item or a node (the store's query, the
drop logic), or the flow direction a node has by virtue of which end of an edge it sits on (the
DEXPI 2.0 fallback for ``FlowIn``/``FlowOut``). Each of those used to be its own walk over the
list, in seven modules; :class:`ConnectionIndex` is the one walk, and the callers ask it.

The index is a snapshot. A document being edited -- the merge writer rewrites connections as it
reconciles -- rebuilds it at the point of use rather than trusting a stale one; the cost is the
same single pass the old walk was.
"""

from __future__ import annotations

from typing import Iterable, Literal, Mapping

from ..model import DexpiConnection, DexpiDocument, DexpiItem
from .conventions import FLOW_IN, FLOW_OUT, NodeFlow

__all__ = ["ConnectionIndex", "EndRole"]

#: Which end of an edge an endpoint is.
EndRole = Literal["from", "to"]


class ConnectionIndex:
    """Lookups over an edge list, keyed by owner, by item, by node and by flow.

    ``items`` is the document's item map when known; it is only needed by
    :meth:`grouped_by_owner`, which files an edge owned by something that is not an item under
    ``None`` -- the writers' rule for a connection that belongs to no segment.
    """

    def __init__(self, connections: Iterable[DexpiConnection], items: Mapping[str, DexpiItem] | None = None):
        self.connections: list[DexpiConnection] = list(connections)
        self._items = items
        self._by_owner: dict[str | None, list[DexpiConnection]] = {}
        self._by_item: dict[str, list[DexpiConnection]] = {}
        self._by_node: dict[str, list[DexpiConnection]] = {}
        self._flows: dict[str, NodeFlow] = {}
        for connection in self.connections:
            self._by_owner.setdefault(connection.owner_id, []).append(connection)
            for item_id, node_id, flow in (
                (connection.from_item, connection.from_node, FLOW_OUT),
                (connection.to_item, connection.to_node, FLOW_IN),
            ):
                if item_id is not None:
                    self._by_item.setdefault(item_id, []).append(connection)
                if node_id is not None:
                    self._by_node.setdefault(node_id, []).append(connection)
                # First edge wins, in document order: a node is an outlet if fluid leaves it on
                # any edge, and the first statement is the one every consumer has always taken.
                for key in (item_id, node_id):
                    if key is not None:
                        self._flows.setdefault(key, flow)

    @classmethod
    def from_document(cls, doc: DexpiDocument) -> ConnectionIndex:
        return cls(doc.connections, doc.items)

    # -- by owner ----------------------------------------------------------------------------------

    def owned_by(self, owner_id: str | None) -> list[DexpiConnection]:
        """The edges written inside ``owner_id`` -- a segment's own ``<Connection>`` elements -- in
        document order."""
        return list(self._by_owner.get(owner_id, ()))

    def grouped_by_owner(self) -> dict[str | None, list[DexpiConnection]]:
        """Every edge, grouped by the item it is written inside; an owner that is not an item of
        the document (or no owner at all) is keyed under ``None``, which is where the writers emit
        a document-level connection."""
        grouped: dict[str | None, list[DexpiConnection]] = {}
        for connection in self.connections:
            owner = connection.owner_id
            key = owner if self._items is not None and owner in self._items else None
            grouped.setdefault(key, []).append(connection)
        return grouped

    def ends_of(self, owner_id: str) -> list[tuple[str, str | None, EndRole]]:
        """``(item id, node id, role)`` for both ends of every edge ``owner_id`` owns, in document
        order and from-end first, skipping an end that names no item."""
        out: list[tuple[str, str | None, EndRole]] = []
        for connection in self._by_owner.get(owner_id, ()):
            for item_id, node_id, role in (
                (connection.from_item, connection.from_node, "from"),
                (connection.to_item, connection.to_node, "to"),
            ):
                if item_id is not None:
                    out.append((item_id, node_id, role))  # type: ignore[arg-type]
        return out

    # -- by endpoint -------------------------------------------------------------------------------

    def touching(self, item_id: str) -> list[DexpiConnection]:
        """Every edge with ``item_id`` at either end."""
        return list(self._by_item.get(item_id, ()))

    def at_node(self, node_id: str) -> list[DexpiConnection]:
        """Every edge with ``node_id`` at either end."""
        return list(self._by_node.get(node_id, ()))

    # -- flow --------------------------------------------------------------------------------------

    @property
    def flows(self) -> dict[str, NodeFlow]:
        """Nozzle/node ID -> ``"in"`` or ``"out"``, derived from the graph: an item or node at the
        SOURCE end of an edge has fluid leaving it, one at the TARGET end has fluid arriving. Both
        the item id and the node id of each end are keys. A copy; callers may keep it."""
        return dict(self._flows)

    def flow(self, *keys: str | None) -> NodeFlow | None:
        """The flow of the first of ``keys`` the graph has an answer for, or None."""
        for key in keys:
            if key is not None and key in self._flows:
                return self._flows[key]
        return None
