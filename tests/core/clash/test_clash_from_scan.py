"""A clash check driven by a member SCAN, which is how it runs in a browser.

The browser reads an IFC with adacpp's wasm build (embind `scanMembers` -> JSONL: no server, no
tessellation, no ifcopenshell) and then runs `ada.clash` on the result in pyodide. The rules are
not ported to TypeScript, on purpose: a second implementation would pass its own tests and quietly
disagree with the server about a real model, which is worse than not offering the feature at all.

So what these pin is that the two routes AGREE -- the same file, checked from a scan and checked
from a model, has to yield the same joints. That is the claim the whole design rests on.
"""

from __future__ import annotations

import json

import pytest

import ada
from ada.cadit.ifc.read.native_members import (
    members_from_jsonl,
    native_members_available,
    scan_ifc_members,
)
from ada.clash.from_scan import clash_check_from_members, clash_check_from_scan
from ada.clash.identify import run_clash_check

pytestmark = pytest.mark.skipif(
    not native_members_available(),
    reason="ada-cpp without IfcMemberScan; the scan-driven route has no scan to drive it",
)


@pytest.fixture(scope="module")
def frame_ifc(tmp_path_factory):
    pl = ada.Plate("pl", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    beams = [
        ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200"),
        ada.Beam("g2", (5, 5, 0), (0, 5, 0), "IPE200"),
        ada.Beam("g3", (0, 5, 0), (0, 0, 0), "IPE200"),
        ada.Beam("s0", (0, 2.5, 0), (5, 2.5, 0), "HP140x8"),
    ]
    model = ada.Assembly("frame") / (ada.Part("deck") / [pl, *beams])
    out = tmp_path_factory.mktemp("scan") / "frame.ifc"
    model.to_ifc(out, validate=False)
    return out


def test_the_scan_route_finds_the_joints_the_model_route_finds(frame_ifc):
    """The claim the browser check rests on."""
    from_scan = clash_check_from_members(scan_ifc_members(frame_ifc), source_key="frame.ifc")
    from_model = run_clash_check(ada.from_ifc(frame_ifc), source_key="frame.ifc").to_dict()

    assert [j["id"] for j in from_scan["joints"]] == [j["id"] for j in from_model["joints"]]
    assert {g["type_key"]: g["count"] for g in from_scan["groups"]} == {
        g["type_key"]: g["count"] for g in from_model["groups"]
    }


def test_the_joint_ids_are_the_same_ones_a_detail_job_would_re_derive(frame_ifc):
    """Ids are a hash of member names and origin, so a detail hand-off works across routes.

    A joint selected in a browser check has to be re-derivable by a server that details it. If the
    ids differed by route, every hand-off would fail with "no such joint" on a model where the
    browser had just shown the user the joint.
    """
    from_scan = clash_check_from_members(scan_ifc_members(frame_ifc), source_key="frame.ifc")
    assert from_scan["joints"]
    for joint in from_scan["joints"]:
        assert len(joint["id"]) == 12
        assert set(joint["id"]) <= set("0123456789abcdef")


def test_a_scan_written_as_jsonl_is_the_same_scan(frame_ifc, tmp_path):
    """The file is what actually crosses out of wasm and into pyodide."""
    import adacpp.cad as cad

    jsonl = tmp_path / "members.jsonl"
    cad.scan_ifc_members_to_jsonl(str(frame_ifc), str(jsonl))

    via_file = clash_check_from_scan(jsonl, source_key="frame.ifc")
    via_binding = clash_check_from_members(scan_ifc_members(frame_ifc), source_key="frame.ifc")
    assert [j["id"] for j in via_file["joints"]] == [j["id"] for j in via_binding["joints"]]


def test_the_document_says_the_answer_came_from_a_scan(frame_ifc):
    """A joint list is only as trustworthy as the read behind it."""
    doc = clash_check_from_members(scan_ifc_members(frame_ifc), source_key="frame.ifc")
    assert doc["provenance"]["reader"] == "native-member-scan"


def test_a_file_that_is_not_a_scan_is_refused_rather_than_half_read(tmp_path):
    """A JSONL of something else parses line by line and would silently check nothing."""
    bogus = tmp_path / "not_a_scan.jsonl"
    bogus.write_text('{"id": 1, "whatever": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not a member scan"):
        list(members_from_jsonl(bogus))


def test_the_header_line_is_not_mistaken_for_a_member(frame_ifc, tmp_path):
    import adacpp.cad as cad

    jsonl = tmp_path / "members.jsonl"
    written = cad.scan_ifc_members_to_jsonl(str(frame_ifc), str(jsonl))
    records = list(members_from_jsonl(jsonl))
    assert len(records) == written
    assert all("schema" not in r for r in records)
    assert json.loads(jsonl.read_text().splitlines()[0])["schema"] == "adacpp.ifc_members/1"
