"""Regression: empty edge list after whisker drop must not crash.

Some SAT loops in real Genie XML exports consist entirely of paired
"whisker" coedges. ``_drop_whisker_coedges``
removes them, leaving an empty edge list. The previous code indexed
``edges[0]`` on the way into ``get_points`` and exploded with a bare
IndexError, aborting the entire conversion. The fix routes that case
through the same ``ACISInsufficientPointsError`` the surrounding code
already handles with a per-face skip-and-warn.
"""

from __future__ import annotations

import pytest

from ada.cadit.sat.exceptions import ACISInsufficientPointsError
from ada.cadit.sat.read.faces import PlateFactory
from ada.cadit.sat.read.sat_entities import AcisRecord


def test_get_points_on_empty_edges_raises_insufficient_points():
    factory = PlateFactory.__new__(PlateFactory)  # bypass __init__; we don't touch sat_store
    with pytest.raises(ACISInsufficientPointsError):
        factory.get_points([])


class _FakeStore:
    """Minimal SatStore stand-in: resolves ``$N`` / ``N`` refs to records."""

    def __init__(self, records: dict[int, AcisRecord]):
        self._records = records

    def get(self, sat_id):
        if isinstance(sat_id, str):
            sat_id = int(sat_id.lstrip("$"))
        return self._records[sat_id]


def _factory_with(records: dict[int, AcisRecord]) -> PlateFactory:
    factory = PlateFactory.__new__(PlateFactory)
    factory.sat_store = _FakeStore(records)
    return factory


def _build_marker_loop_face() -> tuple[PlateFactory, AcisRecord, AcisRecord]:
    """A SESAM-style face with three loops: a degenerate point-edge marker
    (single self-referential coedge), the real 4-coedge boundary, then another
    marker. Returns (factory, face_record, real_loop)."""
    recs: dict[int, AcisRecord] = {}

    def add(s: str) -> AcisRecord:
        r = AcisRecord.from_string(s)
        recs[r.index] = r
        return r

    # Degenerate marker coedge: next-pointer (idx 6) points to itself.
    add("-10 coedge $-1 -1 -1 $-1 $10 $10 $-1 $1000 forward $100 $-1 #")
    # Real boundary ring: 20->21->22->23->20, four distinct edges.
    add("-20 coedge $-1 -1 -1 $-1 $21 $23 $-1 $2000 forward $101 $-1 #")
    add("-21 coedge $-1 -1 -1 $-1 $22 $20 $-1 $2001 forward $101 $-1 #")
    add("-22 coedge $-1 -1 -1 $-1 $23 $21 $-1 $2002 forward $101 $-1 #")
    add("-23 coedge $-1 -1 -1 $-1 $20 $22 $-1 $2003 forward $101 $-1 #")
    # Second degenerate marker.
    add("-30 coedge $-1 -1 -1 $-1 $30 $30 $-1 $3000 forward $102 $-1 #")
    # Loops chained via next-loop (idx 6); coedge ref at idx 7; none tagged.
    add("-100 loop $-1 -1 -1 $-1 $101 $10 $-1 unknown #")
    real = add("-101 loop $-1 -1 -1 $-1 $102 $20 $-1 unknown #")
    add("-102 loop $-1 -1 -1 $-1 $-1 $30 $-1 unknown #")
    # Face: loop pointer at idx 7 -> first loop.
    face = AcisRecord.from_string("-1 face $-1 -1 -1 $-1 $-1 $100 $-1 $-1 $999 forward #")
    return _factory_with(recs), face, real


def test_single_coedge_ring_is_not_duplicated():
    """A self-referential coedge ring must yield one coedge, not two — else
    ``_drop_whisker_coedges`` mistakes the duplicate for a whisker pair and
    collapses the whole face to zero edges."""
    factory, _face, _real = _build_marker_loop_face()
    marker_loop = factory.sat_store.get("$100")
    edges = factory.get_edges_from_loop(marker_loop)
    assert len(edges) == 1
    # And the real boundary ring walks to exactly its four coedges.
    real_edges = factory.get_edges_from_loop(factory.sat_store.get("$101"))
    assert [e.index for e in real_edges] == [20, 21, 22, 23]


def test_primary_loop_skips_degenerate_marker_loops():
    """When no loop is tagged 'periphery', the boundary loop (most distinct
    edges) wins over the degenerate marker loops — not blindly loops[0]."""
    factory, face, real = _build_marker_loop_face()
    primary = factory._get_primary_loop(face.chunks)
    assert primary is real
