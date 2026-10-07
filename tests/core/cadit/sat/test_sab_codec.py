"""The SAB <-> SAT codec against GeniE V9.3 twins: the same model, saved as text and as binary.

Two oracles, both exact. Rendering the binary body must give the text body GeniE wrote, byte for
byte -- CRLF line ends, the line breaks inside spline data, numbers as ``%.17g`` -- bar the header's
save timestamp; and packing the text body must give the binary body's bytes after the header, byte
for byte, the header itself differing only in that timestamp. Six models, 14 to 203 records, every
record type adapy's writer emits among them (spline-surface/exactsur and pcurve/exppc come from
``curved_plates_resaved``, GeniE's re-save of adapy's own curved-plate workspace; position_attrib
from ``loads_all_kinds``).
"""

from __future__ import annotations

import re
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
    record_words,
    render,
    sat_text_from_sab,
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
    "loads_all_kinds": 69,
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


def _with_date(sat_text: str, date: str) -> str:
    return re.sub(r"(\n18 SESAM - gmGeometry 14 ACIS 33\.0\.1 NT 24 ).{24}", lambda m: m.group(1) + date, sat_text)


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
def test_the_rendered_body_is_the_text_genie_wrote_byte_for_byte(twins, model):
    """Not just the same tokens: the same file. adapy's spline readers read the subtype data line by
    line, so the line breaks and the number forms are what makes the two reads one."""
    binary, text = twins(model)
    date = tokenize(binary)[0]["strings"][2]
    assert sat_text_from_sab(binary) == _with_date(text, date)


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


def test_a_string_is_delimited_by_its_length_not_by_spaces():
    assert record_words("-4 x $1 @8 a b  c d #") == ["-4", "x", "$1", "@8", "a b  c d", "#"]


# -- refused, by name ------------------------------------------------------------------


def test_an_unseen_subtype_is_refused_by_name():
    """A skinned surface GeniE writes as ``rulesur``; adapy never writes one and no twin pins it."""
    with pytest.raises(SabUnsupported, match="rulesur"):
        pack_record("-6 spline-surface $-1 -1 -1 $-1 forward { rulesur } I I I I #")


def test_an_unseen_attribute_flag_word_is_refused_by_name():
    """The 18 action ints of a generic attribute pack into one int whose layout is not decoded;
    only the three words GeniE (and adapy's writer) use are tabled."""
    rec = "-4 string_attrib-name_attrib-gen-attrib $-1 -1 $-1 $-1 $3 2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 0 @6 dnvscp @12 FACE00000001 #"
    with pytest.raises(SabUnsupported, match="flags"):
        pack_record(rec)


def test_a_transform_is_refused_by_name():
    with pytest.raises(SabUnsupported, match="transform"):
        pack_record("-1 transform $-1 -1 -1 $-1 1 0 0 0 1 0 0 0 1 0 0 0 1 no_rotate no_reflect no_shear #")


def test_an_unseen_loop_type_is_refused_by_name():
    """Only ``unknown`` and ``periphery`` are in a twin; the tail after a ``hole`` loop is not."""
    with pytest.raises(SabUnsupported, match="hole"):
        pack_record("-6 loop $-1 -1 -1 $-1 $-1 $11 $3 F hole #")


def test_a_periodic_spline_is_refused_by_name():
    rec = "-19 intcurve-curve $-1 -1 -1 $-1 forward { exactcur full nubs 1 periodic 2 0 1 1 1 0 0 0 1 1 1 0 } I I #"
    with pytest.raises(SabUnsupported, match="periodic"):
        pack_record(rec)


@pytest.mark.parametrize(
    "line1, line2, match",
    [
        ("2000 0 1 1", "18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Tue Oct  6 20:13:24 2026", "history"),
        ("700 0 1 0", "18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Tue Oct  6 20:13:24 2026", "version 700"),
        ("2000 0 1 0", "18 SESAM - gmGeometry 14 ACIS 34.0.1 NT 24 Tue Oct  6 20:13:24 2026", "ACIS 34.0.1"),
    ],
)
def test_a_header_outside_the_measured_one_is_refused(line1, line2, match):
    with pytest.raises(SabUnsupported, match=match):
        pack(f"{line1}\r\n{line2}\r\n1000 9.9999999999999995e-07 1e-10\r\nEnd-of-ACIS-data ")


def test_a_long_string_tag_is_refused_by_name(twins):
    binary, _ = twins("plate")
    i = header_end(binary)
    with pytest.raises(SabUnsupported, match="0x12"):
        tokenize(binary[:i] + bytes([0x12]) + binary[i:])


def test_data_after_the_end_marker_is_refused(twins):
    binary, _ = twins("plate")
    _, records = tokenize(binary + binary[header_end(binary) :])
    with pytest.raises(SabUnsupported, match="after End-of-ACIS-data"):
        render(records)
