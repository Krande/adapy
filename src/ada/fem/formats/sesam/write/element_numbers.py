"""A deck's internal element numbers: 1..n in id order, every element keeping its own id as the external number.

A Sesam deck numbers every element twice (GELMNT1 ``ELNOX ELNO``): the external number is the one people read, the
internal one is what every other record refers to the element by -- GELREF1, BEUSLO, BEISTE, BELOAD1 / BELLO2,
GSETMEMB, SCONMESH, TDELEM -- and Sestra wants it to run 1..n: "Internal element ids must be contiguous in the range
[1, number of elements]" (Sestra 11.3-01, a deck whose spring was element 10000 of 13). The writer used to write the
model's id as both, so a model with a gap in its ids -- an element deleted, a spring or mass numbered apart, one the
deck leaves out -- did not run.

The model's ids are kept as the external numbers, which is what the reader gives back (``read_elements``' internal ->
external map; the SIN reader's ``Numbering``), so a round trip keeps them. Where the ids already run 1..n the two
agree and nothing changes.

Every writer that names an element takes its number from :func:`internal` while ``writer.to_fem`` holds the deck's
numbering (:func:`deck`); outside one -- a writer called on its own -- the id is the number, as before.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Iterable, Iterator

if TYPE_CHECKING:
    from ada.fem import FEM


class ElementNumbers:
    """``internal(id)``: the element's position (1-based) among the deck's element ids, sorted."""

    def __init__(self, ids: Iterable[int]):
        ordered = sorted({int(i) for i in ids})
        contiguous = ordered == list(range(1, len(ordered) + 1))
        self._map = None if contiguous else {eid: n for n, eid in enumerate(ordered, start=1)}
        self.count = len(ordered)

    @property
    def identity(self) -> bool:
        return self._map is None

    def internal(self, el_id) -> int:
        """The internal number of the element with external id ``el_id``; an id the deck does not write is itself."""
        el_id = int(el_id)
        return el_id if self._map is None else self._map.get(el_id, el_id)


_current: list[ElementNumbers] = []


@contextlib.contextmanager
def deck(numbers: ElementNumbers) -> Iterator[ElementNumbers]:
    _current.append(numbers)
    try:
        yield numbers
    finally:
        _current.pop()


def internal(el_id) -> int:
    """The internal number of ``el_id`` in the deck being written (the id itself outside one)."""
    return _current[-1].internal(el_id) if _current else int(el_id)


def deck_element_ids(fem: FEM, ndofs=None, with_springs: bool = True) -> list[int]:
    """The ids of every element the deck writes a GELMNT1 for: the structural elements it has a type for
    (``write_elements.partition_elements``), its springs and its mass elements (``write_point_elements``)."""
    from . import write_point_elements as points
    from .write_elements import partition_elements

    writable, _ = partition_elements(fem)
    ids = [el.id for el in writable]
    if with_springs:
        ids += [sp.id for sp in fem.elements.springs]
    ids += [el.id for el in points.point_elements(fem, ndofs)]
    return ids
