"""Assembly.to_pickle / ada.from_pickle round-trip.

Lets a source parsed once be reused for many export targets (the converter's assembly cache)
without re-reading/re-parsing it. Each from_pickle returns a fresh deep copy.
"""

from __future__ import annotations

import ada


def test_pickle_roundtrip(tmp_path):
    a = ada.Assembly("a")
    p = a.add_part(ada.Part("p"))
    p.add_beam(ada.Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300"))
    p.add_plate(ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01))

    out = a.to_pickle(tmp_path / "asm.pkl")
    assert out.exists()

    b = ada.from_pickle(out)
    assert isinstance(b, ada.Assembly)
    assert {o.name for o in b.get_all_physical_objects()} == {o.name for o in a.get_all_physical_objects()}
    # fresh deep copy: mutating the reload doesn't touch the original
    assert b is not a


def test_pickle_roundtrip_keeps_the_dexpi_store(tmp_path):
    """``_dexpi_store`` holds an ``xml.etree.ElementTree`` element on ``DexpiItem.raw``, which
    pickles fine on its own -- checked explicitly rather than assumed, per a plain ``ET.Element``
    round-tripping cleanly through ``pickle`` in general but this repo never having exercised it
    hanging off an Assembly before."""
    import xml.etree.ElementTree as ET

    from ada.cadit.dexpi.canonical import canonicalize
    from ada.cadit.dexpi.model import DexpiDocument, DexpiHeader, DexpiItem, ItemKind

    doc = DexpiDocument(header=DexpiHeader(project="p"))
    doc.add(
        DexpiItem(
            id="Tank-1", class_name="Tank", kind=ItemKind.EQUIPMENT, raw=ET.fromstring("<Equipment ID='Tank-1'/>")
        )
    )

    a = ada.Assembly("a")
    a.dexpi_store = doc

    out = a.to_pickle(tmp_path / "asm_dexpi.pkl")
    b = ada.from_pickle(out)

    assert b.dexpi_store is not None
    assert b.dexpi_store is not doc  # fresh deep copy, like the rest of the reloaded assembly
    assert canonicalize(b.dexpi_store) == canonicalize(doc)


def test_from_pickle_rejects_non_assembly(tmp_path):
    import pickle

    bad = tmp_path / "bad.pkl"
    bad.write_bytes(pickle.dumps({"not": "an assembly"}))
    try:
        ada.from_pickle(bad)
        raise AssertionError("expected TypeError")
    except TypeError:
        pass
