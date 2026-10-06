"""A workspace whose ACIS body GeniE V9.3 wrote in binary is refused by name.

GeniE V9.3 has a compatibility option, "Write ACIS files in binary format"
(``GenieRules.Compatibility.enable(WriteACISBinaryFile, true)``), under which the
body member of a ``.gnx`` is ``acisGeometry.sab`` -- Standard ACIS Binary, opening
with ``ACIS BinaryFile`` -- instead of the text ``acisGeometry.sat``. GeniE picks the
format by the member name alone, and GeniE before V9.3 cannot open such a workspace.

The fixtures under ``fem_files/sesam/genie93_acis`` are the same models saved by
GeniE V9.3-00 (headless ``GenieRuntime.exe``) once with the option off and once
with it on; only the session user and the local export paths were scrubbed from
``modelData.xml`` / ``modelData.js``, the body members are byte-for-byte what GeniE
wrote. Measured before this refusal existed: ``from_gnx`` on the binary twin
returned a model with every beam and *no plates*, and no error -- the text reader
found no records in the binary body. That silent loss is what these tests pin
against.
"""

from __future__ import annotations

import base64
import io
import pathlib
import zipfile

import pytest

import ada
from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml
from ada.cadit.gxml.write.write_gnx import genie_xml_from_gnx
from ada.cadit.sat.exceptions import ACISBinaryBodyError
from ada.cadit.sat.sab import GNX_BINARY_BODY, GNX_TEXT_BODY, SAB_SIGNATURE, is_sab

MODELS = ["plate", "beams_on_plate", "cylinder_shell", "plate_with_hole", "curved_plates_resaved"]


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


def _members(path: pathlib.Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _plates(a: ada.Assembly) -> dict[str, ada.Plate]:
    return {pl.name: pl for pl in a.get_all_physical_objects(by_type=ada.Plate)}


def _beams(a: ada.Assembly) -> dict[str, ada.Beam]:
    return {bm.name: bm for bm in a.get_all_physical_objects(by_type=ada.Beam)}


# -- the fixtures are what they claim to be -------------------------------------------


@pytest.mark.parametrize("model", MODELS)
def test_twins_differ_only_in_the_body_member(genie93, model):
    """Same GeniE, same model, same members -- except the body's name, format and the option."""
    text, binary = _members(genie93 / f"{model}_text.gnx"), _members(genie93 / f"{model}_binary.gnx")
    assert GNX_TEXT_BODY in text and GNX_BINARY_BODY not in text
    assert GNX_BINARY_BODY in binary and GNX_TEXT_BODY not in binary
    assert set(text) - {GNX_TEXT_BODY} == set(binary) - {GNX_BINARY_BODY}

    assert text[GNX_TEXT_BODY].startswith(b"2000 0 1 0")
    assert binary[GNX_BINARY_BODY].startswith(SAB_SIGNATURE)
    assert is_sab(binary[GNX_BINARY_BODY]) and not is_sab(text[GNX_TEXT_BODY])
    # The same four header integers follow the signature, as little-endian int32.
    assert binary[GNX_BINARY_BODY][len(SAB_SIGNATURE) : len(SAB_SIGNATURE) + 16] == (
        (2000).to_bytes(4, "little") + bytes(4) + (1).to_bytes(4, "little") + bytes(4)
    )

    assert b'<option value="false" option="WriteACISBinaryFile" />' in text["modelData.xml"]
    assert b'<option value="true" option="WriteACISBinaryFile" />' in binary["modelData.xml"]
    assert b"GeniE V9.3-00" in binary["modelData.js"]


# -- the refusal -----------------------------------------------------------------------


@pytest.mark.parametrize("model", MODELS)
def test_a_binary_body_is_refused_by_name(genie93, model):
    with pytest.raises(ACISBinaryBodyError) as excinfo:
        ada.from_gnx(genie93 / f"{model}_binary.gnx")
    message = str(excinfo.value)
    assert f"{model}_binary.gnx" in message
    assert GNX_BINARY_BODY in message
    assert "ACIS BinaryFile" in message
    # The way back: the option by the name GeniE's dialog and its journal use.
    assert "Write ACIS files in binary format" in message
    assert "WriteACISBinaryFile" in message


def test_the_refusal_reaches_every_entry_point(genie93):
    gnx = genie93 / "plate_binary.gnx"
    with pytest.raises(ACISBinaryBodyError):
        ada.from_genie_xml(gnx)
    with pytest.raises(ACISBinaryBodyError):
        genie_xml_from_gnx(gnx)


def test_binary_bytes_under_the_text_name_are_refused_too(genie93, tmp_path):
    """GeniE reads such a member as an empty body; it must not read as one here either."""
    binary = _members(genie93 / "plate_binary.gnx")
    renamed = tmp_path / "renamed.gnx"
    with zipfile.ZipFile(renamed, "w") as z:
        for name, data in binary.items():
            z.writestr(GNX_TEXT_BODY if name == GNX_BINARY_BODY else name, data)
    with pytest.raises(ACISBinaryBodyError) as excinfo:
        ada.from_gnx(renamed)
    assert GNX_TEXT_BODY in str(excinfo.value)


def test_embedded_binary_geometry_is_refused(genie93, tmp_path):
    """A concept XML embedding a binary body. GeniE V9.3 does not write one (its XML export
    embeds text even from a binary workspace), so this guards the seam rather than a file seen."""
    sab = _members(genie93 / "plate_binary.gnx")[GNX_BINARY_BODY]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("b64temp.sab", sab)
    payload = base64.b64encode(buf.getvalue()).decode()
    xml = tmp_path / "embedded.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="ASCII"?>\n<DNV_structure_concept_protocol version="1.6">'
        '<model name="m"><structure_domain><geometry>'
        f'<sat_embedded encoding="base64" compression="zip" tag_name="dnvscp">{payload}</sat_embedded>'
        "</geometry></structure_domain></model></DNV_structure_concept_protocol>\n"
    )
    with pytest.raises(ACISBinaryBodyError) as excinfo:
        get_sat_text_from_xml(xml)
    assert "b64temp.sab" in str(excinfo.value)


# -- the text twin reads whole ---------------------------------------------------------


def test_the_text_twin_reads_the_whole_model(genie93):
    """What the binary twin would have lost in silence: the plate, with its outline."""
    a = ada.from_gnx(genie93 / "beams_on_plate_text.gnx")
    assert sorted(_beams(a)) == ["Bm1", "Bm2", "Bm3"]
    plates = _plates(a)
    assert sorted(plates) == ["Pl1"]
    outline = sorted(tuple(round(float(v), 9) for v in p) for p in plates["Pl1"].poly.points3d)
    assert outline == [(0.0, 0.0, 0.0), (0.0, 3.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0)]
