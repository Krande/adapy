"""The Abaqus/CAE concept-model writer: its refusals, its names, and its guards.

What this file is *not*: a fake Abaqus. Nothing here pretends to know what
``WirePolyLine`` does — that was settled by running the emitted script against a real
Abaqus 2025 kernel, which is the authority and is exercised separately. Two tests below
do ``exec`` the emitted script's own guard functions against a hand-built stand-in for a
CAE part, but only to prove that *the guard logic* rejects an uncovered edge and records
a failure; the stand-in is a fixture for my code, never an oracle for Abaqus'.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import types

import numpy as np
import pytest

import ada
from ada.api.beams.beam_curved import BeamCurved
from ada.api.beams.beam_revolved import BeamRevolve
from ada.api.beams.beam_swept import BeamSweep
from ada.api.beams.beam_tapered import BeamTapered
from ada.api.curves import CurveOpen2d
from ada.api.transforms import Placement
from ada.cadit.cae.names import (
    CaeNameError,
    NameRegistry,
    dump_name_map,
    sanitise_cae_name,
)
from ada.cadit.cae.writer import (
    CaeWriteError,
    UnsupportedBeamError,
    beam_endpoints,
    beam_n1,
    build_plan,
    check_beam_has_no_eccentricity,
    check_beam_is_straight,
)
from ada.sections.concept import GeneralProperties

# --------------------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------------------


def frame(part_origin=(0.0, 0.0, 0.0), beam_order=None):
    """A frame that exercises the traps, with the beams addable in any order.

    ``brace`` lands on the *middle* of ``girder``, which is the imprint that splits the
    through member; ``skew`` is off-axis so its ``n1`` is not axis-aligned; ``UNP200`` is
    asymmetric, on which a sign flip in ``n1`` is visible.
    """
    beams = {
        "col1": ada.Beam("col1", (0, 0, 0), (0, 0, 4), "IPE300"),
        "col2": ada.Beam("col2", (6, 0, 0), (6, 0, 4), "IPE300"),
        "girder": ada.Beam("girder", (0, 0, 4), (6, 0, 4), "HEA300"),
        "brace": ada.Beam("brace", (3, 0, 4), (3, 3, 4), "TUB200x10"),
        "skew": ada.Beam("skew", (0, 0, 0), (6, 3, 4), "UNP200"),
    }
    order = beam_order if beam_order is not None else sorted(beams)
    assert sorted(order) == sorted(beams), "the fixture must add every beam exactly once"
    part = ada.Part("Frame", placement=Placement(origin=part_origin))
    for name in order:
        part.add_beam(beams[name])
    assembly = ada.Assembly("Acceptance")
    assembly.add_part(part)
    return assembly


def a_plate(name="pl1"):
    return ada.Plate(name, [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))


def one_beam(**beam_kwargs):
    part = ada.Part("P")
    part.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300", **beam_kwargs))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


def emit(assembly, tmp_path, name="out.py", **kwargs):
    written = assembly.to_abaqus_cae_script(tmp_path / name, **kwargs)
    return written, written[0].read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Names — CAE rejects a dot, and silently replaces a duplicate
# --------------------------------------------------------------------------------------


def test_a_dot_is_the_character_that_has_to_go():
    """Measured: ``m.Material(name='has.dot')`` fails with ``invalid name``."""
    assert sanitise_cae_name("brace.1") == "brace_1"
    assert sanitise_cae_name("a.b.c") == "a_b_c"


@pytest.mark.parametrize("name", ["has space", "has-dash", "PL_1/2", "aeø", "a" * 80])
def test_everything_cae_actually_accepts_is_left_alone(name):
    """Also measured: no 38- or 80-char limit in 2025, and these characters are fine.

    A sanitiser that reached for them would rename objects for no reason, and a rename
    is what breaks the trail from a CAE result back to a GeniE member.
    """
    assert sanitise_cae_name(name) == name


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_name_is_refused_rather_than_emitted(blank):
    with pytest.raises(CaeNameError, match="blank"):
        sanitise_cae_name(blank)


def test_the_same_unique_name_twice_is_a_collision_not_a_reuse():
    """CAE would silently replace the first object; probed, and it invalidates its handle."""
    registry = NameRegistry("parts")
    registry.allocate_unique("Frame")
    with pytest.raises(CaeNameError, match="both named 'Frame'"):
        registry.allocate_unique("Frame")


def test_two_names_that_sanitise_together_collide():
    registry = NameRegistry("sets")
    assert registry.allocate_unique("a.b") == "a_b"
    with pytest.raises(CaeNameError, match="both sanitise to 'a_b'"):
        registry.allocate_unique("a_b")


def test_a_shared_name_is_idempotent_but_still_collides_on_a_different_original():
    registry = NameRegistry("materials")
    assert registry.allocate_shared("S355") == "S355"
    assert registry.allocate_shared("S355") == "S355"
    registry_2 = NameRegistry("profiles")
    registry_2.allocate_shared("IPE.300")
    with pytest.raises(CaeNameError, match="both sanitise to 'IPE_300'"):
        registry_2.allocate_shared("IPE_300")


def test_the_name_map_records_only_what_changed(tmp_path):
    registry = NameRegistry("sets")
    registry.allocate_unique("brace.1")
    registry.allocate_unique("col1")
    path = tmp_path / "m.name_map.json"
    assert dump_name_map({"sets": registry}, path) is True
    assert json.loads(path.read_text()) == {"sets": {"brace_1": "brace.1"}}


def test_no_name_map_when_nothing_was_renamed(tmp_path):
    registry = NameRegistry("sets")
    registry.allocate_unique("col1")
    path = tmp_path / "m.name_map.json"
    assert dump_name_map({"sets": registry}, path) is False
    assert not path.exists()


def test_a_dotted_beam_name_is_sanitised_and_the_sidecar_says_so(tmp_path):
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("brace.1", (0, 0, 0), (0, 0, 3), "IPE300"))
    written, text = emit(assembly, tmp_path)
    assert [p.name for p in written] == ["out.py", "out.name_map.json"]
    assert "name='brace_1'" in text
    # The original survives only as the comment that makes the rename traceable in the
    # script itself; nothing CAE reads carries the dot it rejects.
    assert "name='brace.1'" not in text
    assert text.count("brace.1") == 1
    assert "    # 'brace.1'" in text
    assert json.loads(written[1].read_text()) == {"sets": {"brace_1": "brace.1"}}


def test_two_beams_whose_names_collide_in_cae_are_refused(tmp_path):
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("b.1", (0, 0, 0), (0, 0, 3), "IPE300"))
    part.add_beam(ada.Beam("b_1", (1, 0, 0), (1, 0, 3), "IPE300"))
    with pytest.raises(CaeNameError, match="both sanitise to 'b_1'"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


# --------------------------------------------------------------------------------------
# Guard 2 — refuse Beam subclasses, by exact type
# --------------------------------------------------------------------------------------


def _refused_beams():
    from ada import CurveRevolve

    return [
        BeamTapered("tapered", (0, 0, 0), (0, 0, 3), "IPE300", "IPE200"),
        BeamCurved("curved", (0, 0, 0), (0, 0, 3), None, "IPE300"),
        BeamRevolve("revolved", CurveRevolve((0, 0, 0), (3, 0, 3), radius=4.0, rot_axis=(0, 1, 0)), "IPE300"),
        BeamSweep(
            "swept",
            CurveOpen2d([(0, 0), (1, 0), (2, 1)], origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1)),
            "IPE300",
        ),
    ]


@pytest.mark.parametrize("bm", _refused_beams(), ids=lambda b: type(b).__name__)
def test_every_beam_subclass_is_refused(bm):
    """And ``isinstance`` would let all four through: that is why the test is exact-type.

    A ``BeamRevolve`` emitted as the straight chord between its endpoints opens, meshes
    and solves, so nothing downstream would report it.
    """
    assert isinstance(bm, ada.Beam), "if this fails the test has stopped testing anything"
    with pytest.raises(UnsupportedBeamError, match=type(bm).__name__):
        check_beam_is_straight(bm)


def test_a_straight_beam_is_not_refused():
    check_beam_is_straight(ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300"))


def test_a_refused_subclass_stops_the_whole_write(tmp_path):
    """No half-written script: the refusal happens while planning, before any text."""
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("ok", (0, 0, 0), (0, 0, 3), "IPE300"))
    part.add_beam(BeamTapered("tapered", (1, 0, 0), (1, 0, 3), "IPE300", "IPE200"))
    destination = tmp_path / "out.py"
    with pytest.raises(UnsupportedBeamError, match="BeamTapered"):
        assembly.to_abaqus_cae_script(destination)
    assert not destination.exists()


# --------------------------------------------------------------------------------------
# Guard 3 — refuse eccentricity
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("end", ["e1", "e2"])
def test_a_beam_with_an_offset_is_refused(end):
    bm = ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300", **{end: (0.0, 0.66, 0.0)})
    with pytest.raises(UnsupportedBeamError, match="non-null {0}".format(end)):
        check_beam_has_no_eccentricity(bm)


def test_an_explicit_zero_offset_is_not_an_offset():
    """adapy stores a ``Direction`` either way, so ``(0,0,0)`` must not refuse a model."""
    check_beam_has_no_eccentricity(ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0, 0, 0), e2=(0, 0, 0)))


def test_no_offset_at_all_is_fine():
    check_beam_has_no_eccentricity(ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300"))


def test_an_eccentric_beam_stops_the_whole_write(tmp_path):
    assembly = one_beam(e1=(0.0, 0.0, 0.66))
    destination = tmp_path / "out.py"
    with pytest.raises(UnsupportedBeamError, match="0.66"):
        assembly.to_abaqus_cae_script(destination)
    assert not destination.exists()


# --------------------------------------------------------------------------------------
# Guard 4 — endpoints from axis_global(), never from n1.p
# --------------------------------------------------------------------------------------


def test_endpoints_follow_the_owning_parts_placement():
    assembly = frame(part_origin=(10.0, 0.0, 0.0))
    bm = assembly.get_part("Frame").beams.from_name("col1")
    assert tuple(bm.n1.p) == (0.0, 0.0, 0.0), "the fixture must have a placement to lose"
    assert beam_endpoints(bm) == ((10.0, 0.0, 0.0), (10.0, 0.0, 4.0))


def test_the_emitted_wires_are_where_axis_global_says_they_are(tmp_path):
    """Raw ``n1.p`` would put this frame 10 m from where the model has it.

    That is the same class of error as the 660 mm one this project already paid for, and
    the emitted script cannot see it: a wire drawn at the wrong origin is still a wire.
    """
    assembly = frame(part_origin=(10.0, 0.0, 0.0))
    _, text = emit(assembly, tmp_path)
    assert "points=(((10.0, 0.0, 0.0), (10.0, 0.0, 4.0)),)" in text
    assert "points=(((0.0, 0.0, 0.0), (0.0, 0.0, 4.0)),)" not in text
    # ... and the in-kernel bounding-box check is stated in the placed coordinates too,
    # so a writer that drew the wires unplaced could not also fool the guard.
    assert "'Frame': ((10.0, 0.0, 0.0), (16.0, 3.0, 4.0))" in text


def test_unit_scale_multiplies_coordinates_and_profile_dimensions(tmp_path):
    _, text = emit(one_beam(), tmp_path, unit_scale=1000.0)
    assert "points=(((0.0, 0.0, 0.0), (0.0, 0.0, 3000.0)),)" in text
    assert "h=300.0" in text  # IPE300, 0.3 m -> 300 mm
    assert "t3=7.1" in text


def general_section(name="GEN"):
    return ada.Section(name, "GENERAL", genprops=GeneralProperties(Ax=0.01, Ix=1e-6, Iy=1e-5, Iz=1e-5))


def test_a_generalized_section_integrates_before_analysis(tmp_path):
    """Measured: with ``integration=DURING_ANALYSIS`` CAE writes

    ``**ERROR -- Generalized Profile cannot be used with this section.`` into the INP it
    exports. The model builds in the GUI and cannot be solved, which is exactly the
    plausible-but-wrong output this writer exists to avoid. Such a section integrates
    BEFORE_ANALYSIS and carries its own E, G, Poisson ratio and density.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm", (0, 0, 0), (0, 0, 3), general_section(), mat=ada.Material("S355")))
    _, text = emit(assembly, tmp_path)
    assert "model.GeneralizedProfile(" in text
    assert "integration=DURING_ANALYSIS" not in text
    assert "integration=BEFORE_ANALYSIS" in text
    assert "poissonRatio=0.3" in text
    assert "density=7850.0" in text
    assert "table=((210000000000.0, 80769230769.23077),)" in text


def test_a_shaped_section_still_integrates_during_analysis(tmp_path):
    """The BEFORE_ANALYSIS form exists for generalised sections only.

    A shaped profile integrated before the analysis would ignore the profile's geometry
    and use the table instead, so the two cases must not be merged.
    """
    _, text = emit(one_beam(), tmp_path)
    assert "integration=DURING_ANALYSIS" in text
    assert "BEFORE_ANALYSIS" not in text


def test_a_section_with_no_faithful_abaqus_shape_is_refused_by_name(tmp_path):
    """``POLY`` raises in the shared mapping: CAE's ArbitraryProfile is thin-walled.

    Measured by WS-A at 0.006 m3 against 0.040 m3 for the filled equivalent, and the
    generalised fallback would come from a ``calc_poly`` stub that returns zeros — a beam
    with no area, which writes and analyses. The refusal has to name the beam, or the
    user is left with a type name and no member.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    poly = ada.Section(
        "PL", "poly", outer_poly=ada.CurvePoly2d([(0, 0), (1, 0), (1, 1)], (0, 0, 0), (1, 0, 0), (0, 0, 1))
    )
    part.add_beam(ada.Beam("odd_one", (0, 0, 0), (0, 0, 3), poly))
    with pytest.raises(CaeWriteError, match="odd_one"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


def test_the_cylinder_is_a_fraction_of_the_member_not_an_absolute_length(tmp_path):
    """The same structure in millimetres must be located the same way.

    An absolute clamp here would be the obvious implementation and would be wrong in
    exactly this writer's characteristic way: 1 mm is a sane radius for a model in metres
    and a hundredth of a micron for the same model in millimetres.
    """
    _, in_metres = emit(one_beam(), tmp_path, name="m.py")
    _, in_millimetres = emit(one_beam(), tmp_path, name="mm.py", unit_scale=1000.0)
    assert "radius=0.0003)" in in_metres
    assert "radius=0.3)" in in_millimetres


def test_a_generalized_profile_refuses_to_be_scaled_by_a_single_factor(tmp_path):
    """Its arguments are an area and second moments: L², L⁴, not L.

    Scaling them linearly would leave every coordinate right and every stiffness wrong
    by orders of magnitude, which is precisely the plausible-but-wrong model this writer
    refuses to emit.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    general = ada.Section("GEN", "GENERAL", genprops=GeneralProperties(Ax=0.01, Ix=1e-6, Iy=1e-5, Iz=1e-5))
    part.add_beam(ada.Beam("bm", (0, 0, 0), (0, 0, 3), general))
    with pytest.raises(CaeWriteError, match="GeneralizedProfile"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py", unit_scale=1000.0)


def test_n1_is_the_beams_yvec_normalised():
    """The INP writer's section data line ends with the same vector, ``fem_sec.local_y``.

    Deriving it any other way here is how a third orientation convention appears.
    """
    for bm in frame().get_part("Frame").beams:
        expected = np.asarray(bm.yvec, dtype=float)
        expected = expected / np.linalg.norm(expected)
        got = np.asarray(beam_n1(bm))
        assert np.allclose(got, expected)
        assert abs(np.linalg.norm(got) - 1.0) < 1e-12
        # Against the EXACT axis the wire is drawn on, not against bm.xvec: Direction
        # rounds xvec to 7 decimals, so a dot product taken against it reads ~4e-8 on a
        # skew member and says nothing about the geometry the script emits.
        start, end = beam_endpoints(bm)
        axis = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
        axis = axis / np.linalg.norm(axis)
        assert abs(float(np.dot(got, axis))) < 1e-12


# --------------------------------------------------------------------------------------
# The emitted script's shape
# --------------------------------------------------------------------------------------


def test_the_preamble_imports_caemodules(tmp_path):
    """Without it ``mdb`` has no geometry importers at all -- measured, not assumed."""
    _, text = emit(one_beam(), tmp_path)
    assert "from abaqus import *" in text
    assert "from abaqusConstants import *" in text
    assert "from caeModules import *" in text


def test_members_are_located_by_a_cylinder_and_never_by_findat(tmp_path):
    """A brace landing mid-span splits the through member: 1 edge -> 3, measured.

    ``findAt`` at a midpoint returns one of the three, so it would section a third of
    the beam and leave the rest bare.
    """
    _, text = emit(frame(), tmp_path)
    assert text.count(".edges.getByBoundingCylinder(") == 5
    assert ".findAt(" not in text  # the call, not the word: the script explains why it is absent


def test_the_cylinder_overshoots_its_members_ends(tmp_path):
    """An end cap exactly on the end vertex can exclude the sub-edge that reaches it."""
    _, text = emit(one_beam(), tmp_path)
    # the member runs (0,0,0) -> (0,0,3)
    assert "center1=(0.0, 0.0, -0.0003), center2=(0.0, 0.0, 3.0003)" in text


def test_every_member_gets_a_set_a_section_and_an_orientation(tmp_path):
    _, text = emit(frame(), tmp_path)
    assert text.count(".Set(name=") == 5
    assert text.count(".SectionAssignment(region=") == 5
    assert text.count(".assignBeamSectionOrientation(region=") == 5
    assert text.count("method=N1_COSINES") == 5


def test_one_beam_section_per_profile_and_material_pair(tmp_path):
    """A BeamSection binds both, so the same profile in two grades is two sections."""
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("a", (0, 0, 0), (0, 0, 3), "IPE300", mat=ada.Material("S355")))
    part.add_beam(ada.Beam("b", (1, 0, 0), (1, 0, 3), "IPE300", mat=ada.Material("S420")))
    _, text = emit(assembly, tmp_path)
    assert text.count("model.IProfile(") == 1
    assert "name='sec_IPE300_S355'" in text
    assert "name='sec_IPE300_S420'" in text


def test_each_part_is_instanced_exactly_once(tmp_path):
    assembly = ada.Assembly("A")
    for part_name in ("Deck", "Jacket"):
        part = assembly.add_part(ada.Part(part_name))
        part.add_beam(ada.Beam("bm_" + part_name, (0, 0, 0), (0, 0, 3), "IPE300"))
    _, text = emit(assembly, tmp_path)
    assert text.count("model.Part(name=") == 2
    assert text.count("assembly.Instance(name=") == 2
    assert "assembly.Instance(name='Deck-1', part=part_0, dependent=ON)" in text
    assert "assembly.Instance(name='Jacket-1', part=part_1, dependent=ON)" in text


def test_the_script_parses_and_stays_inside_the_python_2_syntax_floor(tmp_path):
    """This repo still ships a py2.7 in-Abaqus script, and ``.format()`` costs nothing."""
    _, text = emit(frame(), tmp_path)
    tree = ast.parse(text)
    forbidden = {"JoinedStr", "FormattedValue", "AnnAssign", "NamedExpr"}
    offenders = sorted({type(n).__name__ for n in ast.walk(tree)} & forbidden)
    assert offenders == []


def test_a_destination_that_is_not_a_script_is_refused(tmp_path):
    with pytest.raises(CaeWriteError, match=r"\.py suffix"):
        one_beam().to_abaqus_cae_script(tmp_path / "out.cae")


# --------------------------------------------------------------------------------------
# Untranslated objects, and nothing to translate
# --------------------------------------------------------------------------------------


def test_a_plate_is_listed_as_untranslated_rather_than_dropped_in_silence(tmp_path):
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300"))
    part.add_plate(a_plate())
    _, text = emit(assembly, tmp_path)
    assert "NOT translated by phase 1" in text
    assert "Plate 'pl1'" in text
    assert '"name": "pl1"' in text  # and in the machine-readable result sidecar
    assert "model.Part(name='P'," in text  # the beams are still built


def test_a_part_with_no_beams_becomes_no_cae_part(tmp_path):
    assembly = ada.Assembly("A")
    with_beams = assembly.add_part(ada.Part("Deck"))
    with_beams.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300"))
    empty = assembly.add_part(ada.Part("Empty"))
    empty.add_plate(a_plate())
    _, text = emit(assembly, tmp_path)
    assert text.count("model.Part(name=") == 1
    assert "'Empty'" not in text


def test_a_model_with_no_beams_at_all_is_refused(tmp_path):
    assembly = ada.Assembly("A")
    assembly.add_part(ada.Part("P")).add_plate(a_plate())
    with pytest.raises(CaeWriteError, match="holds no beams"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


def test_a_zero_length_beam_is_refused(tmp_path):
    """adapy itself refuses to construct one, so collapse a valid beam after the fact.

    A wire needs two distinct points; CAE's own error for one is not worth forwarding.
    """
    assembly = ada.Assembly("A")
    bm = assembly.add_part(ada.Part("P")).add_beam(ada.Beam("bm", (1, 1, 1), (1, 1, 4), "IPE300"))
    bm.n2 = ada.Node((1, 1, 1))
    with pytest.raises(CaeWriteError, match="zero length"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


# --------------------------------------------------------------------------------------
# Determinism — WS-C's golden files depend on it
# --------------------------------------------------------------------------------------


def test_the_same_model_written_twice_is_byte_identical(tmp_path):
    _, first = emit(frame(), tmp_path, name="a.py")
    _, second = emit(frame(), tmp_path, name="b.py")
    assert first == second.replace("b.cae", "a.cae").replace("b.cae_build", "a.cae_build")


def test_beams_are_sorted_by_this_writer_not_by_the_container_it_reads():
    """adapy's ``Beams`` container happens to iterate in name order today.

    That is why this test hands the writer an *unsorted* sequence instead. Going through
    the container leaves the writer's own sort untested and free to be deleted — measured:
    a mutation replacing ``sorted(part.beams, ...)`` with ``list(part.beams)`` survived a
    test that added the beams in reverse order — and WS-C's golden files would then
    reorder themselves the next time that container changed.
    """
    assembly = frame()
    part = assembly.get_part("Frame")
    as_given = list(part.beams)
    assert [b.name for b in as_given] == sorted(b.name for b in as_given), "the container is sorted today"
    part._beams = list(reversed(as_given))
    assert [b.name for b in part.beams] != sorted(b.name for b in part.beams)
    names = [m.beam_name for m in build_plan(assembly).parts[0].members]
    assert names == sorted(names)


def test_materials_and_sections_are_emitted_in_name_order(tmp_path):
    """The beam order and the material order disagree here, on purpose.

    With ``a`` carrying S420 and ``b`` S355, visiting the beams in name order visits the
    materials in reverse, so an unsorted emission differs visibly from a sorted one —
    which is what lets this test pin the sort at all.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("a", (0, 0, 0), (0, 0, 3), "IPE300", mat=ada.Material("S420")))
    part.add_beam(ada.Beam("b", (1, 0, 0), (1, 0, 3), "HEA300", mat=ada.Material("S355")))
    plan = build_plan(assembly)
    assert [row.cae_name for row in plan.materials] == ["S355", "S420"]
    assert [s.cae_section_name for s in plan.sections] == ["sec_HEA300_S355", "sec_IPE300_S420"]
    _, text = emit(assembly, tmp_path)
    assert text.index("name='S355'") < text.index("name='S420'")
    assert text.index("name='sec_HEA300_S355'") < text.index("name='sec_IPE300_S420'")


def test_insertion_order_does_not_reach_the_output(tmp_path):
    """The end-to-end form: the same model built two ways emits the same script.

    Weaker than the test above (the container normalises the order before the writer
    sees it), and kept because it is the property WS-C's goldens actually depend on."""
    reversed_order = sorted(["col1", "col2", "girder", "brace", "skew"], reverse=True)
    assert reversed_order != sorted(reversed_order), "the fixture order must not be sorted already"
    plan_sorted = build_plan(frame(beam_order=sorted(reversed_order)))
    plan_reversed = build_plan(frame(beam_order=reversed_order))
    names = [m.beam_name for m in plan_sorted.parts[0].members]
    assert names == sorted(names)
    assert [m.beam_name for m in plan_reversed.parts[0].members] == names
    assert [s.cae_section_name for s in plan_reversed.sections] == [s.cae_section_name for s in plan_sorted.sections]
    assert plan_reversed.materials == plan_sorted.materials


def test_parts_are_emitted_in_name_order_whatever_order_they_were_added(tmp_path):
    def build(order):
        assembly = ada.Assembly("A")
        for part_name in order:
            part = assembly.add_part(ada.Part(part_name))
            part.add_beam(ada.Beam("bm_" + part_name, (0, 0, 0), (0, 0, 3), "IPE300"))
        return assembly

    _, forwards = emit(build(["Alpha", "Zulu"]), tmp_path, name="f.py")
    _, backwards = emit(build(["Zulu", "Alpha"]), tmp_path, name="b.py")
    assert forwards.index("name='Alpha'") < forwards.index("name='Zulu'")
    assert backwards.replace("b.cae", "f.cae").replace("b.cae_build", "f.cae_build") == forwards


# --------------------------------------------------------------------------------------
# Guards 1 and 5 — the emitted script's own checks, exercised against a stand-in part
# --------------------------------------------------------------------------------------


class FakeEdge:
    def __init__(self, index):
        self.index = index
        self.pointOn = ((float(index), 0.0, 0.0),)


class FakeSet:
    def __init__(self, edges):
        self.edges = edges


class FakeVertex:
    def __init__(self, point):
        self.pointOn = (point,)


class FakeAssignment:
    def __init__(self, set_name):
        self.region = (set_name, "FAKE", 1, 1, 0)


class FakePart:
    """A stand-in for what the guard reads back, not a model of CAE.

    The attribute names and the shape of ``sectionAssignments[i].region`` are the ones
    measured on Abaqus 2025: ``region`` is a tuple whose first element is the set name.
    """

    def __init__(self, edge_count, sets, vertices=()):
        self.edges = [FakeEdge(i) for i in range(edge_count)]
        self.sets = {name: FakeSet([self.edges[i] for i in indices]) for name, indices in sets.items()}
        self.sectionAssignments = [FakeAssignment(name) for name in sorted(sets)]
        self.beamSectionOrientations = list(self.sectionAssignments)
        self.vertices = [FakeVertex(p) for p in vertices]


class FakeModel:
    def __init__(self, parts):
        self.parts = parts


def load_emitted_script(text, tmp_path, monkeypatch):
    """``exec`` the emitted script's definitions, without letting it build anything.

    Only the module-level ``try: main()`` is removed; every function the script defines
    is the real one, so what the guards below run is the shipped code, not a paraphrase.
    """
    for module_name, attrs in (
        ("abaqus", {"mdb": object()}),
        (
            "abaqusConstants",
            {
                "CARTESIAN": "CARTESIAN",
                "DEFORMABLE_BODY": "DEFORMABLE_BODY",
                "DURING_ANALYSIS": "DURING_ANALYSIS",
                "IMPRINT": "IMPRINT",
                "N1_COSINES": "N1_COSINES",
                "ON": "ON",
                "THREE_D": "THREE_D",
            },
        ),
        ("caeModules", {}),
    ):
        module = types.ModuleType(module_name)
        for key, value in attrs.items():
            setattr(module, key, value)
        module.__all__ = list(attrs)
        monkeypatch.setitem(sys.modules, module_name, module)

    tree = ast.parse(text)
    before = len(tree.body)
    tree.body = [node for node in tree.body if not isinstance(node, ast.Try)]
    assert len(tree.body) == before - 1, "exactly one module-level try: main() was expected"
    namespace: dict = {"__name__": "emitted_cae_script"}
    exec(compile(tree, "<emitted>", "exec"), namespace)  # noqa: S102 - the script under test
    monkeypatch.chdir(tmp_path)
    exits = []
    monkeypatch.setattr(namespace["os"], "_exit", lambda status: exits.append(status))
    return namespace, exits


def test_guard_one_passes_when_every_edge_carries_a_section(tmp_path, monkeypatch):
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    part = FakePart(3, {"girder": [0, 1], "brace": [2]})
    namespace["_guard_every_edge_sectioned"](FakeModel({"Frame": part}))
    assert exits == []
    assert namespace["_RESULT"]["guards"]["Frame"]["edges_with_no_section"] == 0


def test_guard_one_catches_the_sub_edge_an_imprint_created(tmp_path, monkeypatch):
    """The split half of a through member is the failure this guard exists for.

    Measured against the real kernel too: deleting the girder's assignment from the
    emitted script left ``edges [2, 3] carry none`` and a failed build.
    """
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    part = FakePart(3, {"girder": [0], "brace": [2]})  # edge 1 is the unclaimed half
    namespace["_guard_every_edge_sectioned"](FakeModel({"Frame": part}))
    assert exits == [1]
    result = json.loads((tmp_path / "out.cae_build_result.json").read_text())
    assert result["ok"] is False
    assert "2 of 3 edges carry a section assignment" in result["errors"][0]
    assert "edges [1] carry none" in result["errors"][0]


def test_guard_one_catches_a_part_with_no_geometry_at_all(tmp_path, monkeypatch):
    """An orphan-mesh import reads ``edges 0``; so would a wire build that drew nothing."""
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    namespace["_guard_every_edge_sectioned"](FakeModel({"Frame": FakePart(0, {})}))
    assert exits == [1]
    result = json.loads((tmp_path / "out.cae_build_result.json").read_text())
    assert "has no edges at all" in result["errors"][0]


def test_guard_one_catches_an_edge_claimed_by_two_sections(tmp_path, monkeypatch):
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    part = FakePart(2, {"a": [0, 1], "b": [1]})
    namespace["_guard_every_edge_sectioned"](FakeModel({"Frame": part}))
    assert exits == [1]
    result = json.loads((tmp_path / "out.cae_build_result.json").read_text())
    assert "more than one section assignment" in result["errors"][0]


def test_guard_one_catches_a_section_without_an_orientation(tmp_path, monkeypatch):
    """A member with no ``n1`` is a profile turned an unknown way round: it still solves."""
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    part = FakePart(2, {"a": [0], "b": [1]})
    part.beamSectionOrientations = part.beamSectionOrientations[:1]
    namespace["_guard_every_edge_sectioned"](FakeModel({"Frame": part}))
    assert exits == [1]
    result = json.loads((tmp_path / "out.cae_build_result.json").read_text())
    assert "only 1 beam orientations" in result["errors"][0]


def test_the_bounding_box_guard_catches_geometry_in_the_wrong_place(tmp_path, monkeypatch):
    _, text = emit(frame(part_origin=(10.0, 0.0, 0.0)), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    # Vertices where an unplaced write would have put them: the frame's own local box.
    part = FakePart(1, {"a": [0]}, vertices=[(0.0, 0.0, 0.0), (6.0, 3.0, 4.0)])
    namespace["_guard_bounding_box"](FakeModel({"Frame": part}))
    assert exits == [1]
    result = json.loads((tmp_path / "out.cae_build_result.json").read_text())
    assert "not where adapy said it is" in result["errors"][0]
    assert "worst axis off by 10.0" in result["errors"][0]


def test_the_bounding_box_guard_passes_on_the_placed_coordinates(tmp_path, monkeypatch):
    _, text = emit(frame(part_origin=(10.0, 0.0, 0.0)), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    part = FakePart(1, {"a": [0]}, vertices=[(10.0, 0.0, 0.0), (16.0, 3.0, 4.0)])
    namespace["_guard_bounding_box"](FakeModel({"Frame": part}))
    assert exits == []


def test_an_empty_cylinder_fails_the_build(tmp_path, monkeypatch):
    """A member whose wire was drawn but whose cylinder finds nothing."""
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_member_edges"]("Frame", "girder", [])
    assert exits == [1]
    result = json.loads((tmp_path / "out.cae_build_result.json").read_text())
    assert "contains no edge" in result["errors"][0]


def test_guard_five_records_the_reason_and_forces_a_non_zero_status(tmp_path, monkeypatch):
    """Measured: ``sys.exit(1)`` under ``cae noGUI=`` leaves the run reporting 0.

    ``os._exit(1)`` does reach the caller — and even then the ``abq<ver>.bat`` launcher
    flattens it, so the sidecar is the only reliable signal. Hence both halves here:
    the file, and the forced status.
    """
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_fail"]("something went wrong halfway")
    assert exits == [1]
    sidecar = tmp_path / "out.cae_build_result.json"
    assert sidecar.is_file()
    result = json.loads(sidecar.read_text())
    assert result["ok"] is False
    assert result["errors"] == ["something went wrong halfway"]
    assert result["schema"] == "ada.cae_build_result/1"


def test_the_sidecar_name_follows_the_script_stem(tmp_path):
    _, text = emit(one_beam(), tmp_path, name="jacket_rev32.py")
    assert "RESULT_NAME = 'jacket_rev32.cae_build_result.json'" in text
    assert "CAE_NAME = 'jacket_rev32.cae'" in text


def test_the_writer_returns_only_what_it_wrote(tmp_path):
    written, _ = emit(one_beam(), tmp_path)
    assert written == [tmp_path / "out.py"]
    assert all(isinstance(p, pathlib.Path) for p in written)
