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
from ada.cadit.ifc.read.native_members import ifc_members_to_part, load_members_or_model
from ada.clash.identify import identify_joints
from ada.clash.options import ClashOptions

# DESELECTED where adacpp is absent, not skipped: this module's subject IS the native reader, so
# an env without that kernel was never going to run it, and a skip reports a hole that is covered
# in the env that carries it.
pytestmark = pytest.mark.adacpp


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


# ── material: what the take-off needs and the clash check does not ───────────────────────────


def _scan_reports_material(part) -> bool:
    """Whether the installed adacpp is new enough to state materials (>= 0.29)."""
    from ada.cadit.ifc.read.native_members import materials_are_complete

    return materials_are_complete(part)


def test_the_material_stated_per_member_is_the_one_the_member_gets(frame_ifc):
    from ada.cadit.ifc.read.native_members import ifc_members_to_part

    part = ifc_members_to_part(frame_ifc)
    if not _scan_reports_material(part):
        pytest.skip("ada-cpp without material in IfcMemberScan (needs >= 0.29)")
    # Per MEMBER: the fixture's plate defaults to S420 while its beams are S355, so a reader that
    # took one material for the whole file would be wrong about five objects out of six.
    by_name = {obj.name: obj for obj in part.get_all_physical_objects()}
    assert by_name["pl"].material.name == "S420"
    assert {by_name[n].material.name for n in ("g0", "g1", "g2", "g3", "s0")} == {"S355"}


def test_one_material_object_is_shared_by_every_member_that_names_it(frame_ifc):
    part = ifc_members_to_part(frame_ifc)
    if not _scan_reports_material(part):
        pytest.skip("ada-cpp without material in IfcMemberScan (needs >= 0.29)")
    # A model states a handful of materials and uses each on thousands of members; building one
    # Material per member would put thousands of equal objects in a part that wants a handful.
    girders = [obj for obj in part.get_all_physical_objects() if obj.name.startswith("g")]
    assert len({id(bm.material) for bm in girders}) == 1


def test_the_mass_matches_what_the_full_reader_computes(frame_ifc):
    """The take-off's actual question, asked of both readers."""
    from ada.cadit.ifc.read.native_members import ifc_members_to_part
    from ada.topo_model.takeoff import model_takeoff

    part = ifc_members_to_part(frame_ifc)
    if not _scan_reports_material(part):
        pytest.skip("ada-cpp without material in IfcMemberScan (needs >= 0.29)")
    native = model_takeoff(part)
    full = model_takeoff(ada.from_ifc(frame_ifc))
    assert native["total_mass"] == pytest.approx(full["total_mass"], rel=0.02)


def test_a_defaulted_material_disqualifies_the_native_take_off():
    """Density is not a thing to guess at.

    A member left on its default density still produces a mass -- a plausible one, and a wrong
    one. The take-off would rather pay for the full reader than report that number, so a part
    with any unstated material is refused even though every other question it answers is fine.
    """
    from ada.cadit.ifc.read.native_members import (
        _UNSTATED_MATERIALS,
        materials_are_complete,
    )

    part = ada.Part("p")
    part.metadata[_UNSTATED_MATERIALS] = 0
    assert materials_are_complete(part) is True
    part.metadata[_UNSTATED_MATERIALS] = 3
    assert materials_are_complete(part) is False


@pytest.mark.parametrize("grade_prop", ["Grade", "StrengthGrade"])
def test_either_spelling_of_the_grade_is_read(grade_prop):
    """adapy writes "Grade"; the IFC material-properties convention is "StrengthGrade"."""
    from ada.cadit.ifc.read.native_members import _material_for
    from ada.materials.metals import CarbonSteel

    mat = _material_for(
        {"material": "S420", "material_props": {grade_prop: "S420", "MassDensity": 7850.0}},
        {},
    )
    assert isinstance(mat.model, CarbonSteel)
    assert mat.model.grade == "S420"


def test_an_unlisted_grade_is_carried_rather_than_refused():
    """S235 is a real grade and not one adapy tabulates.

    `CarbonSteel` looks its yield stress up by name, so an unlisted grade raises -- on a file
    that is perfectly valid and that states the yield stress itself two properties along.
    """
    from ada.cadit.ifc.read.native_members import _material_for

    mat = _material_for(
        {"material": "S235", "material_props": {"Grade": "S235", "MassDensity": 7850.0, "YieldStress": 235e6}},
        {},
    )
    assert mat.name == "S235"
    assert mat.model.sig_y == pytest.approx(235e6)


def test_a_member_with_no_stated_material_gets_none_rather_than_a_guess():
    from ada.cadit.ifc.read.native_members import _material_for

    assert _material_for({"material": "", "material_props": {}}, {}) is None
    assert _material_for({}, {}) is None
