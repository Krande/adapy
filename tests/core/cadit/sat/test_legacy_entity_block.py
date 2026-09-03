"""A SAT save format that omits the per-record entity-header block.

Modern ACIS saves write an entity-header block between a record's attribute
pointer and its own data (``<int> <int> $ptr`` for a general entity, a single
``<int>`` for an ATTRIB). Older saves omit it, which shifts every field of
every record three (or one) slots to the left of where this reader's fixed
chunk offsets expect them.

Reading the face's loop pointer at the fixed offset then yields the *surface*
pointer, and the "next loop" walk chases that surface's first coordinate into
an unrelated record and finally reads that record's ``#`` end-of-record marker
as a record id -- ``ValueError: invalid literal for int() with base 10: '#'``.

The records below are synthetic: the minimal closure for one named unit-square
face, written in the block-absent layout.
"""

from __future__ import annotations

import pytest

from ada.cadit.sat.store import SatReaderFactory

# body/lump/shell -> face -> loop -> coedge ring -> edge -> vertex -> point,
# plus the face's plane surface, the edges' straight curves and the face's name
# attribute. Ids are dense so a mis-stepped pointer resolves rather than raising
# a KeyError -- which is what makes the terminator reachable, exactly as in the
# save file this guards against.
LEGACY_SAT = """500 0 1 0
18 synthetic-fixture 14 ACIS 20.0.3 NT 24 Mon Sep 10 07:48:32 2012
1000 9.9999999999999995e-007 1e-010
-0 body $-1 $1 $-1 $-1 #
-1 lump $-1 $-1 $2 $0 #
-2 shell $-1 $-1 $-1 $3 $-1 $1 #
-3 face $4 $-1 $5 $2 $-1 $6 forward double out #
-4 string_attrib-name_attrib-gen-attrib $-1 $-1 $-1 $3 keep keep_kept ignore 6 dnvscp 12 FACE00000001 #
-5 loop $-1 $-1 $7 $3 #
-6 plane-surface $-1 0 0 0 0 0 1 1 0 0 forward_v I I I I #
-7 coedge $-1 $8 $10 $-1 $11 forward $5 $-1 #
-8 coedge $-1 $9 $7 $-1 $12 forward $5 $-1 #
-9 coedge $-1 $10 $8 $-1 $13 forward $5 $-1 #
-10 coedge $-1 $7 $9 $-1 $14 forward $5 $-1 #
-11 edge $-1 $15 0 $16 1 $7 $23 forward 7 unknown #
-12 edge $-1 $16 0 $17 1 $8 $24 forward 7 unknown #
-13 edge $-1 $17 0 $18 1 $9 $25 forward 7 unknown #
-14 edge $-1 $18 0 $15 1 $10 $26 forward 7 unknown #
-15 vertex $-1 $11 $19 #
-16 vertex $-1 $11 $20 #
-17 vertex $-1 $12 $21 #
-18 vertex $-1 $13 $22 #
-19 point $-1 0 0 0 #
-20 point $-1 1 0 0 #
-21 point $-1 1 1 0 #
-22 point $-1 0 1 0 #
-23 straight-curve $-1 0 0 0 1 0 0 I I #
-24 straight-curve $-1 1 0 0 0 1 0 I I #
-25 straight-curve $-1 1 1 0 -1 0 0 I I #
-26 straight-curve $-1 0 1 0 0 -1 0 I I #
End-of-ACIS-data
"""


@pytest.fixture
def legacy_sat(tmp_path):
    path = tmp_path / "legacy_entity_block.sat"
    path.write_text(LEGACY_SAT)
    return path


def test_legacy_face_reads_its_own_loop(legacy_sat):
    """The face's loop pointer must resolve to the loop, not to the surface."""
    saf = SatReaderFactory(legacy_sat)
    face = next(saf.iter_faces())

    loop = saf.sat_store.get(face.chunks[saf.plate_factory.loop_idx])

    assert loop is not None
    assert loop.type == "loop"


def test_legacy_flat_plate_yields_its_corners(legacy_sat):
    """The whole face -> loop -> coedge -> edge -> vertex -> point walk."""
    saf = SatReaderFactory(legacy_sat)
    ((name, points, _edge_curves),) = list(saf.iter_flat_plates())

    assert name == "FACE00000001"
    assert points == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]


def test_legacy_face_surface_is_the_plane(legacy_sat):
    """The surface pointer offset is shifted by the same block, so it is
    covered here too: without the fix it lands on the ``out`` sides token."""
    saf = SatReaderFactory(legacy_sat)
    face = next(saf.iter_faces())

    surface = saf.sat_store.get(face.chunks[10])

    assert surface.type == "plane-surface"
    assert saf.get_named_face_normal("FACE00000001") == (0.0, 0.0, 1.0)
