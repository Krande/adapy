"""The SAB <-> SAT codec against GeniE V9.3 twins: the same model, saved as text and as binary.

Two oracles, both exact. Rendering the binary body must give the text body's records
token for token (numbers by value: the text writer prints ``0`` and ``2.0971012569480099``
where the binary holds the doubles ``0.0`` and ``2.09710125694801``), and packing the text
body must give the binary body's bytes after the header, byte for byte -- the header
itself differs only in the save timestamp. Five models, 14 to 203 records, every record
type adapy's writer emits among them (spline-surface/exactsur and pcurve/exppc come from
``curved_plates_resaved``, GeniE's re-save of adapy's own curved-plate workspace).
"""

from __future__ import annotations

import struct
import zipfile

import pytest

from ada.cadit.sat.sab import GNX_BINARY_BODY, GNX_TEXT_BODY, SAB_SIGNATURE
from ada.cadit.sat.sab_codec import (
    SabUnsupported,
    header_end,
    normalize,
    pack,
    pack_record,
    render,
    text_records,
    tokenize,
)

MODELS = {
    # model -> record count GeniE wrote (the end marker not counted)
    "plate": 28,
    "beams_on_plate": 85,
    "cylinder_shell": 27,
    "plate_with_hole": 40,
    "curved_plates_resaved": 203,
}


@pytest.fixture
def twins(fem_files):
    d = fem_files / "sesam" / "genie93_acis"

    def _load(model: str) -> tuple[bytes, str]:
        with zipfile.ZipFile(d / f"{model}_binary.gnx") as z:
            binary = z.read(GNX_BINARY_BODY)
        with zipfile.ZipFile(d / f"{model}_text.gnx") as z:
            text = z.read(GNX_TEXT_BODY).decode()
        return binary, text

    return _load


@pytest.mark.parametrize("model", sorted(MODELS))
def test_header_carries_the_text_header_fields(twins, model):
    binary, text = twins(model)
    hdr, _ = tokenize(binary)
    line1, line2, line3 = text_records(text)[0]
    assert [hdr["version"], hdr["n_records"], hdr["n_entities"], hdr["flags"]] == [int(x) for x in line1.split()]
    assert hdr["strings"][:2] == ["SESAM - gmGeometry", "ACIS 33.0.1 NT"]
    assert "ACIS 33.0.1 NT" in line2
    assert hdr["doubles"] == [float(x) for x in line3.split()]
    # The four header integers are raw little-endian int32 straight after the signature.
    assert struct.unpack_from("<4i", binary, len(SAB_SIGNATURE)) == (2000, 0, 1, 0)


@pytest.mark.parametrize("model", sorted(MODELS))
def test_rendering_the_binary_body_gives_the_text_body(twins, model):
    binary, text = twins(model)
    _, records = tokenize(binary)
    rendered = render(records)
    _, expected = text_records(text)
    assert rendered[-1] == "End-of-ACIS-data"
    assert len(rendered) - 1 == len(expected) == MODELS[model]
    differing = [(i, r, t) for i, (r, t) in enumerate(zip(rendered, expected)) if normalize(r) != normalize(t)]
    assert (
        differing == []
    ), f"first difference, record {differing[0][0]}:\n  bin: {differing[0][1]}\n  txt: {differing[0][2]}"


@pytest.mark.parametrize("model", sorted(MODELS))
def test_packing_the_text_body_gives_the_binary_bytes(twins, model):
    binary, text = twins(model)
    packed = pack(text)
    hb, hp = header_end(binary), header_end(packed)
    assert packed[hp:] == binary[hb:], f"{model}: {len(packed) - hp} packed bytes vs {len(binary) - hb}"
    # And the header, bar the timestamp GeniE stamped on each save.
    date_bin = tokenize(binary)[0]["strings"][2]
    date_txt = tokenize(packed)[0]["strings"][2]
    assert packed[:hp].replace(date_txt.encode(), b"") == binary[:hb].replace(date_bin.encode(), b"")


def test_an_unseen_subtype_is_refused_by_name():
    """A skinned surface GeniE writes as ``rulesur``; adapy never writes one and no twin pins it."""
    with pytest.raises(SabUnsupported, match="rulesur"):
        pack_record("-6 spline-surface $-1 -1 -1 $-1 forward { rulesur } I I I I #")


def test_an_unseen_attribute_flag_word_is_refused_by_name():
    """The 18 action ints of a generic attribute pack into one int whose layout is not decoded;
    only the two words GeniE (and adapy's writer) use are tabled."""
    rec = "-4 string_attrib-name_attrib-gen-attrib $-1 -1 $-1 $-1 $3 2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 0 @6 dnvscp @12 FACE00000001 #"
    with pytest.raises(SabUnsupported, match="flags"):
        pack_record(rec)
