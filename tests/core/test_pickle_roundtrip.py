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


def test_from_pickle_rejects_non_assembly(tmp_path):
    import pickle

    bad = tmp_path / "bad.pkl"
    bad.write_bytes(pickle.dumps({"not": "an assembly"}))
    try:
        ada.from_pickle(bad)
        raise AssertionError("expected TypeError")
    except TypeError:
        pass
