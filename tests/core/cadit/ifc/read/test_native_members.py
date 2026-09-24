"""Reading an IFC's MEMBERS natively: same answer as the full reader, without the geometry.

A clash check asks what meets what. That is a question about beams and plates, and `ada.from_ifc`
answers it only after reading the file with ifcopenshell and building every solid on the way past
-- the expensive half, which this caller throws away. `IfcMemberScan` (adacpp) answers it from the
entities that state it, and `native_members` turns that into the same `Beam`/`Plate` objects, so
every pass downstream runs unchanged.

What these pin is PARITY, not the reader's internals: the same members, and the same joints, out
of one file read two ways. A faster reader that found different joints would be worthless.
"""

from __future__ import annotations

import collections

import pytest

import ada
from ada.cadit.ifc.read.native_members import (
    ifc_members_to_part,
    load_members_or_model,
    native_members_available,
)
from ada.clash.identify import identify_joints
from ada.clash.options import ClashOptions

pytestmark = pytest.mark.skipif(
    not native_members_available(),
    reason="ada-cpp without IfcMemberScan (needs >= 0.27); the reader falls back to from_ifc there",
)


@pytest.fixture(scope="module")
def frame_ifc(tmp_path_factory):
    """A plate on four eccentric girders with a stringer -- beams, plates and a placement chain."""
    pl = ada.Plate("pl", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    girders = [
        ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
        ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
        ada.Beam("g2", (5, 5, 0), (0, 5, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
        ada.Beam("g3", (0, 5, 0), (0, 0, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
    ]
    stringer = ada.Beam("s0", (0, 2.5, 0), (5, 2.5, 0), "HP140x8")
    model = ada.Assembly("frame") / (ada.Part("deck") / [pl, *girders, stringer])
    out = tmp_path_factory.mktemp("native_members") / "frame.ifc"
    model.to_ifc(out, validate=False)
    return out


def _members(part):
    return (
        sorted(b.name for b in part.get_all_physical_objects(by_type=ada.Beam)),
        sorted(p.name for p in part.get_all_physical_objects(by_type=ada.Plate)),
    )


def test_the_same_members_come_out_as_the_full_reader_finds(frame_ifc):
    assert _members(ifc_members_to_part(frame_ifc)) == _members(ada.from_ifc(frame_ifc))


def test_sections_are_resolved_from_their_catalogue_names(frame_ifc):
    beams = {b.name: b for b in ifc_members_to_part(frame_ifc).get_all_physical_objects(by_type=ada.Beam)}
    # The family is what a connection spec matches on, so a section read as the wrong family is a
    # wrong answer rather than a missing one.
    assert beams["g0"].section.type is ada.Section.TYPES.IPROFILE
    assert beams["s0"].section.type is ada.Section.TYPES.ANGULAR  # HP bulb flat
    assert beams["g0"].section.h == pytest.approx(0.2)


def test_a_plate_is_rebuilt_in_world_coordinates(frame_ifc):
    plates = list(ifc_members_to_part(frame_ifc).get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) == 1
    pl = plates[0]
    assert pl.t == pytest.approx(0.01)
    corners = {tuple(round(float(v), 6) for v in p) for p in pl.poly.points3d}
    assert corners == {(0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)}


def test_the_clash_check_finds_the_same_joints_either_way(frame_ifc):
    # The fact that matters. Everything above is the means.
    def counts(model):
        outcome = identify_joints(model, ClashOptions(include_plate_joints=True))
        return collections.Counter(f.origin for f in outcome.joints)

    native = counts(ifc_members_to_part(frame_ifc))
    full = counts(ada.from_ifc(frame_ifc))
    assert native == full
    assert native["plate-beam"] > 0, "the fixture must exercise the plate passes"


def test_a_non_ifc_source_goes_to_the_full_reader(tmp_path):
    called = {}

    def fallback(path, ext):
        called["path"], called["ext"] = path, ext
        return "full-model"

    assert load_members_or_model(tmp_path / "model.stp", ".stp", fallback) == "full-model"
    assert called["ext"] == ".stp"


def test_an_ifc_the_scan_cannot_read_falls_back_rather_than_reporting_nothing(tmp_path):
    # It does not raise -- it reads as ZERO members, which is exactly what a file that genuinely
    # has none reads as. Indistinguishable from this side, so the full reader decides; otherwise a
    # check would answer "this source has no beams or plates" with confidence about a file it
    # never parsed.
    broken = tmp_path / "broken.ifc"
    broken.write_text("not an ifc at all")
    assert load_members_or_model(broken, ".ifc", lambda p, e: "full-model") == "full-model"
