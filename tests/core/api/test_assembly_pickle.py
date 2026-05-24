"""Pickle round-trip guards for Assembly.

The IFC caches (`Assembly._ifc_store`, `Assembly._source_ifc_files`) hold
C-bound ifcopenshell objects that don't pickle. The `__getstate__` override
clears them before serialize; these tests confirm the round trip survives.
"""

from __future__ import annotations

import pickle

import ada


def _build_assembly():
    bm = ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300")
    return ada.Assembly("cantilever") / bm


def test_fresh_assembly_pickles():
    a = _build_assembly()
    b = pickle.loads(pickle.dumps(a))

    assert b.name == "cantilever"
    beams = list(b.get_all_physical_objects())
    assert len(beams) == 1
    assert beams[0].name == "bm1"


def test_assembly_pickles_after_ifc_store_access():
    a = _build_assembly()
    # populate the IFC cache; this is where the non-picklable handles
    # would otherwise sneak into Assembly.__dict__
    _ = a.ifc_store

    b = pickle.loads(pickle.dumps(a))

    # caches reset on the deserialized side
    assert b._ifc_store is None
    assert b._ifc_file is None
    assert b._source_ifc_files == {}

    # but lazy access still works on the reborn assembly
    assert b.ifc_store is not None
    assert b._ifc_store is not None


def test_assembly_pickle_preserves_beam_geometry():
    a = _build_assembly()
    b = pickle.loads(pickle.dumps(a))

    src_bm = next(iter(a.get_all_physical_objects()))
    dst_bm = next(iter(b.get_all_physical_objects()))

    assert tuple(src_bm.n1.p) == tuple(dst_bm.n1.p)
    assert tuple(src_bm.n2.p) == tuple(dst_bm.n2.p)
    assert src_bm.section.name == dst_bm.section.name


def test_ifc_store_direct_pickle_is_stub():
    """Direct IfcStore pickle yields a stub; .f and friends are cleared."""
    a = _build_assembly()
    store = a.ifc_store

    restored = pickle.loads(pickle.dumps(store))

    assert restored.f is None
    assert restored.owner_history is None
    assert restored.writer is None
    assert restored.reader is None
    assert restored.callback is None
    # settings is rebuilt via the default factory so callers don't see None
    assert restored.settings is not None
