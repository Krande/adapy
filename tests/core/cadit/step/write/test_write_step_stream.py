"""Streaming AP242 STEP writer (Part.to_stp(writer="stream")).

Validation routes through the CAD backend abstraction (active_backend) so it
runs under OpenCASCADE or adacpp -- the streaming writer itself touches no kernel.
"""

import ada
from ada import Beam, Plate, Section


def _roundtrip_solids(path):
    """Read a STEP file via the active CAD backend and return (n_solids, n_invalid)."""
    from ada.cad import active_backend

    be = active_backend()
    shape = be.read_step_bytes(open(path, "rb").read())
    solids = be.solids(shape)
    n_invalid = sum(0 if be.is_valid(s) else 1 for s in solids)
    return len(solids), n_invalid


def _roundtrip_names(path):
    """Read member names back via the XCAF step reader (the assembly tree)."""
    from ada.cad.doc import active_doc_backend

    store = active_doc_backend().step_reader(str(path))
    return {shp.name for shp in store.iter_all_shapes(True)}


def _model():
    tub = Beam("tub", (0, 0, 0), (0, 0, 3), Section("tub", from_str="TUB300x20"))  # hollow circle
    box = Beam("box", (1, 0, 0), (1, 0, 3), Section("box", from_str="BOX400x400x20x20"))
    ipe = Beam("ipe", (2, 0, 0), (6, 0, 0), Section("ipe", from_str="IPE300"))  # poly + fillets
    pl = Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    return ada.Assembly("m") / (ada.Part("pp") / [tub, box, ipe, pl])


def test_stream_writer_emits_valid_solids(tmp_path):
    a = _model()
    out = tmp_path / "stream.stp"
    stats = a.to_stp(out, writer="stream")

    assert stats == {"emitted": 4, "skipped": 0}
    assert out.exists() and out.stat().st_size > 0

    n_solids, n_invalid = _roundtrip_solids(out)
    assert n_solids == 4
    assert n_invalid == 0


def test_stream_writer_member_names_roundtrip(tmp_path):
    a = _model()
    out = tmp_path / "stream_named.stp"
    a.to_stp(out, writer="stream")

    names = _roundtrip_names(out)
    assert {"tub", "box", "ipe", "pl"}.issubset(names)


def test_stream_writer_ap214_schema(tmp_path):
    a = _model()
    out = tmp_path / "stream214.stp"
    a.to_stp(out, writer="stream", schema="AP214")
    assert "AUTOMOTIVE_DESIGN" in out.read_text()
    n_solids, n_invalid = _roundtrip_solids(out)
    assert n_solids == 4 and n_invalid == 0


def test_stream_writer_rejects_unknown(tmp_path):
    a = _model()
    import pytest

    with pytest.raises(ValueError):
        a.to_stp(tmp_path / "x.stp", writer="bogus")
