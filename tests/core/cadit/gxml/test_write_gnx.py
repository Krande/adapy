"""The Genie workspace (.gnx) writer.

A workspace is what Genie itself saves: a zip of the concept XML plus its
ACIS body as separate members. The OS associates .gnx with Genie, so a
workspace opens straight into the program where a concept XML must be
imported by hand — that is the whole point of writing one.
"""

import xml.etree.ElementTree as ET
import zipfile

import ada
from ada.cadit.gxml.write.write_gnx import gnx_from_genie_xml

GNX_MEMBERS = {
    "lastUsedLicenseFileName.txt",
    "assemblyType.txt",
    "modelData.js",
    "acisGeometry.sat",
    "acisFaceFacets.bin",
    "modelData.xml",
}


def _build():
    p = ada.Part("P") / (
        ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300"),
        ada.Plate("pl1", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.2),
        ada.Plate("pl2", [(10, 0), (20, 0), (20, 10), (10, 10)], 0.2),
    )
    return ada.Assembly("a") / p


def _members(gnx_path):
    with zipfile.ZipFile(gnx_path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_gnx_is_the_workspace_genie_saves(tmp_path):
    gnx = tmp_path / "plate_and_beam.gnx"
    _build().to_gnx(gnx)

    m = _members(gnx)
    assert set(m) == GNX_MEMBERS
    assert m["lastUsedLicenseFileName.txt"] == b"GENIE"
    assert m["assemblyType.txt"] == b"0"
    assert m["acisFaceFacets.bin"] == b"\x00\x00\x00\x00"
    assert m["modelData.js"].startswith(b"//")

    sat = m["acisGeometry.sat"].decode()
    assert sat.splitlines()[0].startswith("2000 0 1 0")
    assert sat.rstrip().endswith("End-of-ACIS-data")
    assert " face " in sat, "a model with plates must carry a real body, not the empty one"

    root = ET.fromstring(m["modelData.xml"])
    assert root.tag == "DNV_structure_concept_protocol"
    # The model name is the workspace name: what Genie shows in the title bar.
    assert root.find("./model").get("name") == "plate_and_beam"
    plates = root.findall(".//flat_plate")
    assert len(plates) == 2
    # Plates reference faces IN the SAT member — never a body embedded in the XML.
    refs = [f.get("face_ref") for f in root.findall(".//sat_reference/face")]
    assert len(refs) == 2
    for ref in refs:
        assert ref in sat
    assert root.find(".//sat_embedded_sequence") is None
    assert root.find(".//sat_embedded") is None
    assert len(root.findall(".//straight_beam")) == 1


def test_gnx_without_plates_carries_the_empty_body(tmp_path):
    a = ada.Assembly("a") / (ada.Part("P") / ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300"))
    gnx = tmp_path / "beams_only.gnx"
    a.to_gnx(gnx)

    m = _members(gnx)
    sat = m["acisGeometry.sat"].decode()
    assert "-0 body $-1 -1 -1 $-1 $-1 $-1 $-1 F #" in sat
    assert sat.rstrip().endswith("End-of-ACIS-data")
    root = ET.fromstring(m["modelData.xml"])
    assert len(root.findall(".//straight_beam")) == 1


def test_gnx_matches_the_embedded_xml_export(tmp_path):
    """The workspace's XML is the embed_sat export with the body lifted out,
    and the body is byte-for-byte what the XML would have embedded."""
    from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml

    xml = tmp_path / "m.xml"
    gnx = tmp_path / "m.gnx"
    _build().to_genie_xml(xml, embed_sat=True)
    _build().to_gnx(gnx)

    embedded = get_sat_text_from_xml(xml).replace("\r\n", "\n")
    lifted = _members(gnx)["acisGeometry.sat"].decode().replace("\r\n", "\n")
    # The ACIS header carries a timestamp; the entity lines below it must match.
    assert embedded.splitlines()[3:] == lifted.splitlines()[3:]


def test_repack_lifts_an_embedded_body_out_of_a_concept_xml(tmp_path):
    xml = tmp_path / "embedded.xml"
    _build().to_genie_xml(xml, embed_sat=True)
    assert ET.parse(xml).getroot().find(".//sat_embedded_sequence") is not None

    gnx = gnx_from_genie_xml(xml)
    assert gnx == tmp_path / "embedded.gnx"
    m = _members(gnx)
    assert set(m) == GNX_MEMBERS
    root = ET.fromstring(m["modelData.xml"])
    assert root.find(".//sat_embedded_sequence") is None
    assert root.find("./model").get("name") == "embedded"
    assert len(root.findall(".//sat_reference/face")) == 2
    assert " face " in m["acisGeometry.sat"].decode()


def test_repack_of_a_polygon_xml_gets_the_empty_body(tmp_path):
    xml = tmp_path / "polygons.xml"
    _build().to_genie_xml(xml, embed_sat=False)
    m = _members(gnx_from_genie_xml(xml, tmp_path / "out.gnx"))
    assert "-0 body $-1" in m["acisGeometry.sat"].decode()
    root = ET.fromstring(m["modelData.xml"])
    assert len(root.findall(".//flat_plate")) == 2


def test_gnx_reads_back_as_the_same_model(tmp_path):
    """``from_genie_xml`` takes a workspace too: the body is embedded back into
    the XML member and read the way a concept XML is."""
    gnx = tmp_path / "rt.gnx"
    _build().to_gnx(gnx)
    a = ada.from_genie_xml(gnx)
    assert len(list(a.get_all_physical_objects(by_type=ada.Beam))) == 1
    plates = list(a.get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) == 2
    assert {pl.t for pl in plates} == {0.2}


def test_unpacked_workspace_is_the_embedded_xml_again(tmp_path):
    from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml
    from ada.cadit.gxml.write.write_gnx import genie_xml_from_gnx

    gnx = tmp_path / "u.gnx"
    _build().to_gnx(gnx)
    xml = genie_xml_from_gnx(gnx)
    assert xml == tmp_path / "u.xml"
    root = ET.parse(xml).getroot()
    assert root.find(".//sat_embedded_sequence") is not None
    assert " face " in get_sat_text_from_xml(xml)

    # Beams only: nothing to embed, and the reader must not be handed an empty body.
    beams = tmp_path / "b.gnx"
    (ada.Assembly("a") / (ada.Part("P") / ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300"))).to_gnx(beams)
    root = ET.parse(genie_xml_from_gnx(beams)).getroot()
    assert root.find(".//sat_embedded_sequence") is None
    assert len(list(ada.from_genie_xml(beams).get_all_physical_objects(by_type=ada.Beam))) == 1


def test_convert_page_offers_gnx_wherever_it_offers_xml():
    from ada.comms.rest.converter import supported_targets_for

    for key in ("m/a.xml", "m/a.ifc", "m/a.fem", "m/a.inp", "m/a.step", "m/a.sat"):
        targets = supported_targets_for(key)
        assert "xml" in targets, key
        assert "gnx" in targets, key


def test_rest_converter_serves_gnx(fem_files):
    """A FEM deck through the worker's convert() entry point comes back as a
    workspace with concept objects rebuilt from the mesh."""
    from ada.comms.rest.converter import convert, result_bytes

    out = convert(fem_files / "sesam/beamMassT1.FEM", "models/x.FEM", "gnx")
    data = result_bytes(out)
    assert data[:2] == b"PK"
    tmp = fem_files.parent / "_gnx_probe.gnx"
    try:
        tmp.write_bytes(data)
        m = _members(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    assert set(m) == GNX_MEMBERS
    root = ET.fromstring(m["modelData.xml"])
    assert root.tag == "DNV_structure_concept_protocol"
    assert len(root.findall(".//straight_beam")) > 0
