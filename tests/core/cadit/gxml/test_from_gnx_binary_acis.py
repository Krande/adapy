"""A workspace whose ACIS body GeniE V9.3 wrote in binary reads as the same model as its text twin.

GeniE V9.3 has a compatibility option, "Write ACIS files in binary format"
(``GenieRules.Compatibility.enable(WriteACISBinaryFile, true)``), under which the
body member of a ``.gnx`` is ``acisGeometry.sab`` -- Standard ACIS Binary, opening
with ``ACIS BinaryFile`` -- instead of the text ``acisGeometry.sat``. GeniE picks the
format by the member name alone, and GeniE before V9.3 cannot open such a workspace.

The fixtures under ``fem_files/sesam/genie93_acis`` are the same models saved by
GeniE V9.3-00 (headless ``GenieRuntime.exe``) once with the option off and once
with it on; only the session user and the local export paths were scrubbed from
``modelData.xml`` / ``modelData.js``, the body members are byte-for-byte what GeniE
wrote. ``loads_all_kinds`` is ``genie_loads_all_kinds.xml`` (16 load cases, a
combination, a support) imported into V9.3 with a point mass added; its body also
carries GeniE's ``position_attrib`` records. ``skinned_surface_binary`` holds a
``rulesur`` surface, which no twin pins and so must be refused.

Measured before binary bodies were read: ``from_gnx`` on a binary twin returned a
model with every beam and *no plates*, and no error. These tests pin the opposite:
the binary twin is the text twin's model, entity for entity, or a refusal by name.

``cylinder_shell`` reads as one ``PlateCurved`` on its cylinder and ``plate_with_hole``
as one ``PlateCurved`` with the hole as an inner loop (see ``test_gxml_plate_round_trip``),
in text and binary alike.
"""

from __future__ import annotations

import base64
import io
import logging
import pathlib
import re
import struct
import zipfile

import pytest

import ada
from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml
from ada.cadit.gxml.write.write_gnx import genie_xml_from_gnx
from ada.cadit.sat.exceptions import ACISBinaryBodyError
from ada.cadit.sat.sab import GNX_BINARY_BODY, GNX_TEXT_BODY, SAB_SIGNATURE, is_sab

from .gnx_fingerprint import model_fingerprint

#: model -> (beams, plates) the text twin reads to: the comparison must not pass on two empty reads.
MODELS = {
    "plate": (0, 1),
    "beams_on_plate": (3, 1),
    "cylinder_shell": (0, 1),
    "plate_with_hole": (0, 1),
    "curved_plates_resaved": (0, 9),
    "loads_all_kinds": (3, 1),
}


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


@pytest.fixture
def ada_log():
    """adapy's logger does not propagate, so caplog cannot see it; collect its records directly."""
    records: list[logging.LogRecord] = []
    handler = logging.Handler(level=logging.WARNING)
    handler.emit = records.append
    logger = logging.getLogger("ada")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def _members(path: pathlib.Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _repack(path: pathlib.Path, members: dict[str, bytes]) -> pathlib.Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _counts(a: ada.Assembly) -> tuple[int, int]:
    # Part.plates holds PlateCurved too, which is not a Plate subclass.
    parts = a.get_all_subparts()
    return sum(len(p.beams) for p in parts), sum(len(p.plates) for p in parts)


def _without_date(sat_text: str) -> str:
    # The header's save timestamp is the one thing that differs between the two saves.
    return re.sub(r"24 \w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4} ", "24 <date> ", sat_text)


# -- the fixtures are what they claim to be -------------------------------------------


@pytest.mark.parametrize("model", sorted(MODELS))
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


# -- the binary twin is the text twin's model ------------------------------------------


@pytest.mark.parametrize("model", sorted(MODELS))
def test_the_binary_twin_reads_as_the_text_twin(genie93, model):
    text = ada.from_gnx(genie93 / f"{model}_text.gnx", build_topology_store=True)
    binary = ada.from_gnx(genie93 / f"{model}_binary.gnx", build_topology_store=True)
    assert _counts(text) == MODELS[model]
    assert _counts(binary) == MODELS[model]
    assert binary._topology_store.summary() == text._topology_store.summary()
    expected, got = model_fingerprint(text), model_fingerprint(binary)
    differing = sorted(k for k in set(expected) | set(got) if expected.get(k) != got.get(k))
    assert differing == [], f"{model}: {differing[:5]}"


def test_loads_supports_and_masses_survive_the_binary_body(genie93):
    """The model with everything a concept read carries besides geometry, so the comparison above
    is not one between two models that have none: 16 load cases, a combination, a support, a mass,
    and the surface loads that name their plate -- which only resolve when the plate was read."""
    a = ada.from_gnx(genie93 / "loads_all_kinds_binary.gnx")
    (part,) = a.get_all_subparts()
    loads = part.concept_fem.loads
    assert len(loads.load_cases) == 16
    assert len(loads.load_case_combinations) == 1
    assert len(part.concept_fem.constraints.point_constraints) == 1
    assert len(part.masses) == 1
    pressures = [ld for lc in loads.load_cases.values() for ld in lc.loads if hasattr(ld, "plate_ref")]
    assert pressures and all(ld.plate_ref is not None for ld in pressures)


def test_every_entry_point_reads_the_binary_body(genie93, tmp_path):
    gnx = genie93 / "beams_on_plate_binary.gnx"
    expected = model_fingerprint(ada.from_gnx(genie93 / "beams_on_plate_text.gnx"))
    assert model_fingerprint(ada.from_genie_xml(gnx)) == expected
    xml = genie_xml_from_gnx(gnx, tmp_path / "unpacked.xml")
    assert model_fingerprint(ada.from_genie_xml(xml)) == expected


def test_the_unpacked_xml_embeds_the_text_genie_wrote(genie93, tmp_path):
    """The binary body is rendered to the very text GeniE saves with the option off, bar the date."""
    xml = genie_xml_from_gnx(genie93 / "curved_plates_resaved_binary.gnx", tmp_path / "unpacked.xml")
    text = _members(genie93 / "curved_plates_resaved_text.gnx")[GNX_TEXT_BODY].decode()
    assert _without_date(get_sat_text_from_xml(xml)) == _without_date(text.replace("\r", ""))


# -- bytes and names that disagree -----------------------------------------------------


def test_binary_bytes_under_the_text_name_are_read_and_flagged(genie93, tmp_path, ada_log):
    """GeniE reads such a member as an empty body (measured). Nothing GeniE writes looks like it,
    so adapy reads what the bytes are, and says that GeniE would not."""
    binary = _members(genie93 / "plate_binary.gnx")
    renamed = _repack(
        tmp_path / "renamed.gnx", {GNX_TEXT_BODY if n == GNX_BINARY_BODY else n: d for n, d in binary.items()}
    )
    assert _counts(ada.from_gnx(renamed)) == MODELS["plate"]
    assert any("binary SAB under a text name" in r.getMessage() for r in ada_log)


def test_text_bytes_under_the_binary_name_are_read_and_flagged(genie93, tmp_path, ada_log):
    text = _members(genie93 / "plate_text.gnx")
    renamed = _repack(
        tmp_path / "renamed.gnx", {GNX_BINARY_BODY if n == GNX_TEXT_BODY else n: d for n, d in text.items()}
    )
    assert _counts(ada.from_gnx(renamed)) == MODELS["plate"]
    assert any("SAT text under a binary name" in r.getMessage() for r in ada_log)


def test_a_workspace_with_both_bodies_is_refused(genie93, tmp_path):
    """GeniE writes one or the other; which it would read from both is not known."""
    members = _members(genie93 / "plate_text.gnx")
    members[GNX_BINARY_BODY] = _members(genie93 / "plate_binary.gnx")[GNX_BINARY_BODY]
    with pytest.raises(ACISBinaryBodyError, match="both"):
        ada.from_gnx(_repack(tmp_path / "both.gnx", members))


def test_embedded_binary_geometry_is_read(genie93, tmp_path):
    """A concept XML embedding a binary body. GeniE V9.3 does not write one (its XML export
    embeds text even from a binary workspace), so this covers the seam rather than a file seen."""
    sab = _members(genie93 / "plate_binary.gnx")[GNX_BINARY_BODY]
    text = _members(genie93 / "plate_text.gnx")[GNX_TEXT_BODY].decode()
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
    assert _without_date(get_sat_text_from_xml(xml)) == _without_date(text.replace("\r", ""))


# -- what is refused, by name ----------------------------------------------------------


def _assert_names_the_way_back(message: str, origin: str, member: str = GNX_BINARY_BODY) -> None:
    assert origin in message
    assert member in message
    assert "ACIS BinaryFile" in message
    # The way back: the option by the name GeniE's dialog and its journal use.
    assert "Write ACIS files in binary format" in message
    assert "WriteACISBinaryFile" in message


def test_a_skinned_surface_is_refused_by_name(genie93):
    """``rulesur`` (CreateSkinningSurface): its field layout is in no twin, so it is not guessed."""
    with pytest.raises(ACISBinaryBodyError) as excinfo:
        ada.from_gnx(genie93 / "skinned_surface_binary.gnx")
    message = str(excinfo.value)
    assert "rulesur" in message
    _assert_names_the_way_back(message, "skinned_surface_binary.gnx")


def test_a_history_section_is_refused_by_name(genie93, tmp_path):
    """Header flags other than 0 announce a history section, which GeniE does not write."""
    members = _members(genie93 / "plate_binary.gnx")
    sab = bytearray(members[GNX_BINARY_BODY])
    struct.pack_into("<i", sab, len(SAB_SIGNATURE) + 12, 1)
    members[GNX_BINARY_BODY] = bytes(sab)
    with pytest.raises(ACISBinaryBodyError) as excinfo:
        ada.from_gnx(_repack(tmp_path / "history.gnx", members))
    assert "history" in str(excinfo.value)
    _assert_names_the_way_back(str(excinfo.value), "history.gnx")


def test_a_truncated_body_is_refused_not_read_in_part(genie93, tmp_path):
    members = _members(genie93 / "beams_on_plate_binary.gnx")
    members[GNX_BINARY_BODY] = members[GNX_BINARY_BODY][:-40]
    with pytest.raises(ACISBinaryBodyError):
        ada.from_gnx(_repack(tmp_path / "truncated.gnx", members))


def test_an_unknown_record_type_is_refused_not_skipped(genie93, tmp_path):
    """A record type outside the programs (here a renamed ``vertex``) stops the read: skipping it
    would shift every later record's index and every pointer to it."""
    members = _members(genie93 / "plate_binary.gnx")
    assert members[GNX_BINARY_BODY].count(b"\x0d\x06vertex") == 4
    members[GNX_BINARY_BODY] = members[GNX_BINARY_BODY].replace(b"\x0d\x06vertex", b"\x0d\x06vortex", 1)
    with pytest.raises(ACISBinaryBodyError) as excinfo:
        ada.from_gnx(_repack(tmp_path / "vortex.gnx", members))
    assert "'vortex'" in str(excinfo.value)
    _assert_names_the_way_back(str(excinfo.value), "vortex.gnx")
