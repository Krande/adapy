"""The parsed document, and the accessors every consumer of it wants.

:class:`DexpiStore` is to DEXPI what :class:`~ada.cadit.gxml.store.GxmlStore` is to Genie XML: it
owns the parsed model and hands out the pieces, so a caller never carries a bare ``ElementTree``
around. The one thing it does that ``GxmlStore`` does not is *dispatch* -- DEXPI ships in two
serializations, and which one a file is in is settled by its root tag alone, so the store sniffs it
and picks the reader rather than asking the caller to know.

The ``iter_*`` accessors are all thin filters over ``document.items``. They exist because the
alternative is every caller re-deriving "which of these are equipment" from the DEXPI supertype
graph, and getting it subtly wrong: there is no DEXPI class called ``Equipment``, and ``Chamber``
and ``Nozzle`` do not derive from ``ProcessEquipment``.

Nothing here is imported by ``ada/__init__.py``.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from typing import Iterator

from .canonical import canonicalize, graph_signature
from .flavour import sniff_flavour
from .model import (
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiItem,
    DexpiNode,
    ItemKind,
)
from .read import read_dexpi20, read_proteus
from .validate import validate_document

__all__ = ["DexpiStore", "read_dexpi"]

_READERS = {
    DexpiFlavour.PROTEUS: read_proteus,
    DexpiFlavour.DEXPI20: read_dexpi20,
}


def read_dexpi(
    source: str | pathlib.Path | ET.Element | ET.ElementTree,
    flavour: DexpiFlavour | str | None = None,
) -> DexpiDocument:
    """Parse a DEXPI file of either flavour.

    The flavour is sniffed from the root tag -- ``<PlantModel>`` is Proteus, ``<Model>`` is DEXPI
    2.0 -- which costs one start tag rather than a parse. Pass ``flavour`` to override it.
    """
    resolved = DexpiFlavour(flavour) if flavour is not None else sniff_flavour(source)
    return _READERS[resolved](source)


class DexpiStore:
    """A parsed DEXPI document plus the ways of getting at it.

    ::

        store = DexpiStore("plant.xml")
        store.flavour                       # DexpiFlavour.PROTEUS or .DEXPI20
        [item.tag for item in store.iter_equipment()]
        store.validate()                    # structural problems, as messages

    ``source`` is a path, an already-parsed element or tree, or a :class:`DexpiDocument` that has
    been built or read elsewhere. ``flavour`` overrides the sniff, for the rare file whose root tag
    lies about it.
    """

    def __init__(
        self,
        source: str | pathlib.Path | ET.Element | ET.ElementTree | DexpiDocument,
        flavour: DexpiFlavour | str | None = None,
    ):
        if isinstance(source, DexpiDocument):
            self._document = source
        else:
            self._document = read_dexpi(source, flavour=flavour)

        self._path = pathlib.Path(self._document.source) if self._document.source else None

    # -- identity ----------------------------------------------------------------------------------

    @property
    def document(self) -> DexpiDocument:
        """The parsed document. The store adds no state of its own -- this is the whole model."""
        return self._document

    @property
    def path(self) -> pathlib.Path | None:
        """Where the document was read from, or None if it was built in memory."""
        return self._path

    @property
    def flavour(self) -> DexpiFlavour:
        return self._document.flavour

    @property
    def header(self):
        return self._document.header

    @property
    def warnings(self) -> list[str]:
        """What the reader could not make sense of but carried on past."""
        return self._document.warnings

    def validate(self) -> list[str]:
        """Structural problems in the document. An empty list means it is internally consistent."""
        return validate_document(self._document)

    def canonicalize(self) -> dict:
        """The round-trip oracle: did this document survive a write and re-read unchanged?"""
        return canonicalize(self._document)

    def graph_signature(self) -> dict:
        """The convergence oracle: is this the same P&ID, whichever flavour it was written in?"""
        return graph_signature(self._document)

    # -- items -------------------------------------------------------------------------------------

    def item(self, item_id: str) -> DexpiItem | None:
        return self._document.items.get(item_id)

    def iter_items(self, kind: ItemKind | None = None) -> Iterator[DexpiItem]:
        """Every item, or every item of one kind, in document order."""
        for item in self._document.items.values():
            if kind is None or item.kind is kind:
                yield item

    def iter_roots(self) -> Iterator[DexpiItem]:
        for item_id in self._document.root_ids:
            item = self._document.items.get(item_id)
            if item is not None:
                yield item

    def iter_equipment(self) -> Iterator[DexpiItem]:
        """Process equipment: tanks, pumps, columns, exchangers.

        Classified by ``is_a(cls, "ProcessEquipment")``, so a chamber or a nozzle -- which come
        straight off ``Core/ConceptualObject`` and are *not* process equipment despite living
        inside one -- is not included. Use :meth:`iter_chambers` and :meth:`iter_nozzles` for those.
        """
        return self.iter_items(ItemKind.EQUIPMENT)

    def iter_chambers(self) -> Iterator[DexpiItem]:
        return self.iter_items(ItemKind.CHAMBER)

    def iter_nozzles(self) -> Iterator[DexpiItem]:
        return self.iter_items(ItemKind.NOZZLE)

    def iter_piping_systems(self) -> Iterator[DexpiItem]:
        return self.iter_items(ItemKind.PIPING_SYSTEM)

    def iter_piping_segments(self) -> Iterator[DexpiItem]:
        """The two-ended runs a P&ID is actually made of, and what adapy routes one system per."""
        return self.iter_items(ItemKind.PIPING_SEGMENT)

    def iter_piping_components(self) -> Iterator[DexpiItem]:
        return self.iter_items(ItemKind.PIPING_COMPONENT)

    def iter_off_page_connectors(self) -> Iterator[DexpiItem]:
        """Where the drawing ends and the rest of the plant begins -- adapy's site terminals."""
        return self.iter_items(ItemKind.OFF_PAGE_CONNECTOR)

    def iter_instrumentation(self) -> Iterator[DexpiItem]:
        return self.iter_items(ItemKind.INSTRUMENTATION)

    def iter_by_class(self, class_name: str) -> Iterator[DexpiItem]:
        """Every item whose class is, or inherits from, ``class_name``."""
        yield from self._document.by_class(class_name)

    def children_of(self, item_id: str) -> list[DexpiItem]:
        return self._document.children(item_id)

    def by_tag(self, tag: str) -> list[DexpiItem]:
        """Items carrying ``tag``. A list, because a P&ID may legitimately tag two things alike."""
        return [item for item in self._document.items.values() if item.tag == tag]

    # -- connectivity ------------------------------------------------------------------------------

    def iter_connections(self) -> Iterator[DexpiConnection]:
        yield from self._document.connections

    def iter_nodes(self, *, process_only: bool = True) -> Iterator[tuple[DexpiItem, DexpiNode]]:
        """Every connection point with the item that owns it.

        ``process_only`` drops the Proteus symbol anchor, which is what a caller generating adapy
        ports wants; pass False to see the nodes exactly as the document numbers them.
        """
        for item in self._document.items.values():
            for node in item.nodes:
                if process_only and node.is_anchor:
                    continue
                yield item, node

    def connections_of(self, item_id: str) -> list[DexpiConnection]:
        """Every edge with ``item_id`` at either end."""
        return [c for c in self._document.connections if item_id in (c.from_item, c.to_item)]

    def __repr__(self) -> str:
        return (
            f"DexpiStore(flavour={self.flavour.value}, items={len(self._document.items)}, "
            f"connections={len(self._document.connections)}, path={str(self._path)!r})"
        )
