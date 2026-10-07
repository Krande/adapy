"""Writing a workspace whose ACIS body is binary: ``to_gnx(..., binary_acis=True)``.

Text stays the default. GeniE before V9.3 opens a binary workspace as an empty model without a
message (measured on V8.13-02), while every GeniE reads text.

What GeniE V9.3-00 was measured to do with adapy's workspaces (rt: 2 beams + a plate; the nine
curved plates of ``curved_plates.xml``; the ``loads_all_kinds`` model), each written as text, as
binary with the option, and as binary without it: every variant imports whole; GeniE picks the
format by the member name, so the binary body reads without the option. But a workspace *opened*
and saved again in GeniE keeps its binary body only when the XML says
``<option value="true" option="WriteACISBinaryFile" />``; without it GeniE re-saves text. Hence
the writer sets the option for a binary body, in the minimal ``rules/compatibility`` block GeniE
was measured to accept.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

import pytest

import ada
from ada.cadit.gxml.write.write_gnx import (
    _body_member,
    genie_xml_from_gnx,
    gnx_from_genie_xml,
)
from ada.cadit.sat.exceptions import ACISBinaryBodyError
from ada.cadit.sat.sab import GNX_BINARY_BODY, GNX_TEXT_BODY, is_sab
from ada.cadit.sat.sab_codec import header_end, pack

from .gnx_fingerprint import model_fingerprint

OPTION_PATH = "./model/rules/compatibility/compatibility_options/option[@option='WriteACISBinaryFile']"


def _build() -> ada.Assembly:
    p = ada.Part("P") / (
        ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300"),
        ada.Plate("pl1", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.2),
        ada.Plate("pl2", [(10, 0), (20, 0), (20, 10), (10, 10)], 0.2),
    )
    return ada.Assembly("a") / p


def _members(path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _options(xml: bytes) -> list[str]:
    return [o.get("value") for o in ET.fromstring(xml).findall(OPTION_PATH)]


@pytest.fixture(params=["beams_and_plates", "curved_plates", "loads_all_kinds"])
def model(request, fem_files) -> ada.Assembly:
    if request.param == "beams_and_plates":
        return _build()
    if request.param == "curved_plates":
        return ada.from_genie_xml(fem_files / "sesam" / "curved_plates.xml")
    return ada.from_gnx(fem_files / "sesam" / "genie93_acis" / "loads_all_kinds_text.gnx")


def test_text_is_the_default_and_carries_no_option(tmp_path):
    gnx = _build().to_gnx(tmp_path / "t.gnx")
    m = _members(gnx)
    assert GNX_TEXT_BODY in m and GNX_BINARY_BODY not in m
    assert not is_sab(m[GNX_TEXT_BODY])
    # The text workspace is what it was before the option existed: no compatibility block at all.
    assert ET.fromstring(m["modelData.xml"]).find("./model/rules") is None


def test_binary_writes_the_sab_member_and_sets_the_option(tmp_path):
    text = _members(_build().to_gnx(tmp_path / "t.gnx"))
    binary = _members(_build().to_gnx(tmp_path / "b.gnx", binary_acis=True))
    assert GNX_BINARY_BODY in binary and GNX_TEXT_BODY not in binary
    assert set(binary) - {GNX_BINARY_BODY} == set(text) - {GNX_TEXT_BODY}
    assert is_sab(binary[GNX_BINARY_BODY])
    assert _options(binary["modelData.xml"]) == ["true"]
    # The body is the text body packed: same bytes after the header (the header differs in its date).
    packed = pack(text[GNX_TEXT_BODY].decode())
    assert binary[GNX_BINARY_BODY][header_end(binary[GNX_BINARY_BODY]) :] == packed[header_end(packed) :]


def test_binary_and_text_workspaces_read_as_one_model(model, tmp_path):
    # One workspace name for both: it becomes the model's (part's) name.
    text = ada.from_gnx(model.to_gnx(tmp_path / "text" / "m.gnx"), build_topology_store=True)
    binary = ada.from_gnx(model.to_gnx(tmp_path / "binary" / "m.gnx", binary_acis=True), build_topology_store=True)
    expected, got = model_fingerprint(text), model_fingerprint(binary)
    assert any(k.startswith("plate ") for k in expected), "a model with no plates proves nothing about the body"
    differing = sorted(k for k in set(expected) | set(got) if expected.get(k) != got.get(k))
    assert differing == [], differing[:5]


def test_the_streaming_route_writes_binary_too(tmp_path):
    gnx = _build().to_gnx(tmp_path / "binary" / "s.gnx", streaming=True, binary_acis=True)
    m = _members(gnx)
    assert GNX_BINARY_BODY in m and _options(m["modelData.xml"]) == ["true"]
    expected = model_fingerprint(ada.from_gnx(_build().to_gnx(tmp_path / "text" / "s.gnx", streaming=True)))
    assert model_fingerprint(ada.from_gnx(gnx)) == expected


def test_repacking_a_genie_xml_makes_its_option_match_the_body(fem_files, tmp_path):
    """A GeniE export carries the option itself (twice, set to true in a binary workspace): repacked
    as text it must say false, or GeniE would re-save the text workspace as binary."""
    xml = genie_xml_from_gnx(fem_files / "sesam" / "genie93_acis" / "loads_all_kinds_binary.gnx", tmp_path / "u.xml")
    assert _options(xml.read_bytes()) == ["true", "true"]
    text = _members(gnx_from_genie_xml(xml, tmp_path / "t.gnx"))
    assert GNX_TEXT_BODY in text and _options(text["modelData.xml"]) == ["false", "false"]
    binary = _members(gnx_from_genie_xml(xml, tmp_path / "b.gnx", binary_acis=True))
    assert GNX_BINARY_BODY in binary and _options(binary["modelData.xml"]) == ["true", "true"]


def test_a_body_the_packer_cannot_represent_is_refused(tmp_path):
    sat = (
        "2000 0 1 0\r\n18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Tue Oct  6 20:13:24 2026\r\n"
        "1000 9.9999999999999995e-07 1e-10\r\n-0 spline-surface $-1 -1 -1 $-1 forward { rulesur } I I I I #\r\n"
        "End-of-ACIS-data "
    )
    assert _body_member(tmp_path / "x.gnx", sat, binary_acis=False) == (GNX_TEXT_BODY, sat)
    with pytest.raises(ACISBinaryBodyError, match="rulesur.*binary_acis=False"):
        _body_member(tmp_path / "x.gnx", sat, binary_acis=True)


def test_every_writer_entry_point_defaults_to_text(tmp_path):
    from ada.cadit.gxml.write.write_gnx import write_gnx

    (part,) = _build().get_all_subparts()
    assert GNX_TEXT_BODY in _members(write_gnx(part, tmp_path / "w.gnx"))
    assert GNX_TEXT_BODY in _members(_build().to_gnx(tmp_path / "s.gnx", streaming=True))
    xml = _build().to_genie_xml(tmp_path / "x.xml", embed_sat=True)
    assert GNX_TEXT_BODY in _members(gnx_from_genie_xml(xml, tmp_path / "r.gnx"))
