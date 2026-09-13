"""GeniE concept XML that declares ASCII but is not ASCII.

Every GeniE export stamps ``<?xml version="1.0" encoding="ASCII"?>`` on, but GeniE
writes text fields in the Windows ANSI codepage it ran under. One object named with
a non-ASCII character is a byte expat rejects, and it used to abort the whole
conversion of an otherwise perfectly well-formed file. The user cannot fix the
export, so the reader has to cope.

``files/fem_files/sesam/mis_declared_ascii.xml`` is a miniature of the 518 kB user
file that surfaced this: the same declaration, and a single 0xA7 (``§``) byte inside
an attribute value.
"""

import xml.etree.ElementTree as ET

import pytest

from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml
from ada.cadit.gxml.xml_parse import genie_xml_root_from_bytes, read_genie_xml_root


@pytest.fixture
def mis_declared_xml(example_files):
    return (example_files / "fem_files/sesam/mis_declared_ascii.xml").resolve().absolute()


def test_fixture_still_reproduces_the_defect(mis_declared_xml):
    """Guards the fixture itself: if a stray re-save turned it into plain UTF-8
    (or ASCII), the tests below would pass without exercising any recovery."""
    with pytest.raises(ET.ParseError):
        ET.parse(str(mis_declared_xml))


def test_mis_declared_ascii_is_recovered(mis_declared_xml):
    root = read_genie_xml_root(mis_declared_xml)

    assert root.find(".//report").attrib["title"] == "§Genie_CBA_HTV_NoRigidLink"


def test_sat_extraction_survives_mis_declared_ascii(mis_declared_xml):
    # The entry point in the reported traceback. The SAT blob is base64 and so
    # never carried the bad byte — it was the surrounding document that failed.
    assert "End-of-ACIS-History-Data" in get_sat_text_from_xml(mis_declared_xml)


def test_utf8_mis_declared_as_ascii_is_not_mojibaked(tmp_path):
    """A file that is genuinely UTF-8 must come back as UTF-8.

    Re-reading it through an ANSI codepage would "succeed" while silently turning
    every multi-byte character into two wrong ones — worse than the ParseError.
    """
    xml_file = tmp_path / "utf8_declared_ascii.xml"
    xml_file.write_bytes('<?xml version="1.0" encoding="ASCII"?>\n<model name="Kjøl Ås" />\n'.encode("utf-8"))

    assert read_genie_xml_root(xml_file).attrib["name"] == "Kjøl Ås"


def test_genuinely_broken_xml_still_raises(tmp_path):
    """Tolerant decoding must not become tolerant parsing."""
    xml_file = tmp_path / "broken.xml"
    xml_file.write_bytes(b'<?xml version="1.0" encoding="ASCII"?>\n<model><beam name="b1"></model>\n')

    with pytest.raises(ET.ParseError):
        read_genie_xml_root(xml_file)


def test_recovery_also_applies_to_in_memory_xml(mis_declared_xml):
    # The `modelData.xml` member of a .gnx workspace never touches the filesystem,
    # and GeniE writes it with the same declaration and the same disregard for it.
    root = genie_xml_root_from_bytes(mis_declared_xml.read_bytes(), "modelData.xml")

    assert root.find(".//report").attrib["title"] == "§Genie_CBA_HTV_NoRigidLink"
