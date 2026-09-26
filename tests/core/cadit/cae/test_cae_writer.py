"""The Abaqus/CAE concept-model writer: its refusals, its names, and its guards.

What this file is *not*: a fake Abaqus. Nothing here pretends to know what
``WirePolyLine`` does — that was settled by running the emitted script against a real
Abaqus 2025 kernel, which is the authority and is exercised separately. Several tests
below do ``exec`` the emitted script's own guard functions against a hand-built stand-in
for a CAE part, but only to prove that *the guard logic* rejects what it is supposed to and
records a failure; the stand-in is a fixture for my code, never an oracle for Abaqus'.

Where a stand-in's edge and vertex counts come from a real measurement (a disconnected
frame's 5 edges and 10 vertices, a crossing's 4 and 5, a broken collinear joint's 2 and 4)
the docstring says so, because those numbers are the only part of the fixture that has to
be true of Abaqus rather than merely convenient.
"""

from __future__ import annotations

import ast
import json
import math
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
from ada.api.curves import CurveOpen2d, CurveRevolve
from ada.api.transforms import Placement
from ada.cadit.cae.curves import (
    MAX_TURN_RADIANS,
    CurveNotSupported,
    sample_member_curve,
    sample_member_legs,
)
from ada.cadit.cae.names import (
    CaeNameError,
    NameRegistry,
    dump_name_map,
    sanitise_cae_name,
)
from ada.cadit.cae.topology import Segment, find_crossings
from ada.cadit.cae.writer import (
    CURVE_N1_MIN_SIN,
    CaeWriteError,
    UnsupportedBeamError,
    beam_endpoints,
    beam_n1,
    beam_section_offset,
    build_plan,
    check_beam_shape,
    check_n1_holds_along_the_curve,
    curved_beam_section_offset,
)
from ada.geom import curves as gc
from ada.sections.concept import GeneralProperties

# --------------------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------------------


def frame(part_origin=(0.0, 0.0, 0.0), beam_order=None):
    """A frame that exercises the traps, with the beams addable in any order.

    ``brace`` lands on the *middle* of ``girder``, which is the imprint that splits the
    through member; ``skew`` is off-axis so its ``n1`` is not axis-aligned.

    ``UNP200`` is asymmetric, and it is **not** here to make a sign flip in ``n1``
    visible: it cannot. Every second moment of area is quadratic in position and so
    invariant under a 180° rotation, which means ``n1`` and ``−n1`` give identical
    bending stiffness on *any* section and no deflection measurement can tell them apart.
    The sign is pinned only by the cross-writer equality against the INP writer's ``n1``
    (`test_cae_orientation_convention.py`). What an asymmetric section does catch, and no
    symmetric one can, is a **mirrored** local frame — ``n2 = n1 × t`` where the
    convention is ``t × n1`` — because that reverses the product of inertia.

    Built and measured in Abaqus 2025: 6 edges and 7 vertices, the girder split in two by
    the landing brace.
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


def exact_arc_spline(radius=2.0, start=0.0, end=math.pi / 2.0, z=0.0):
    """A rational quadratic B-spline that **is** a circular arc, not a fit to one.

    This is the form an ACIS ``intcurve`` takes and therefore what adapy's Genie reader hands
    a ``BeamCurved``: three control points and a middle weight of ``cos(half sweep)``. Using
    an exact arc rather than an arbitrary spline is what lets these tests compare the emitted
    arc length against a closed-form number -- ``radius * sweep`` -- instead of against
    another sampling of the same code.
    """
    half = (end - start) / 2.0
    weight = math.cos(half)
    middle_radius = radius / weight
    middle_angle = start + half
    return gc.RationalBSplineCurveWithKnots(
        degree=2,
        control_points_list=[
            (radius * math.cos(start), radius * math.sin(start), z),
            (middle_radius * math.cos(middle_angle), middle_radius * math.sin(middle_angle), z),
            (radius * math.cos(end), radius * math.sin(end), z),
        ],
        curve_form=gc.BSplineCurveFormEnum.CIRCULAR_ARC,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3, 3],
        knots=[0.0, 1.0],
        knot_spec=gc.KnotType.UNSPECIFIED,
        weights_data=[1.0, weight, 1.0],
    )


def a_curved_beam(kind="BeamCurved", name=None, sec="IPE300", **kwargs):
    """One member of each curved class, all three describing a quarter circle of radius 2.

    The same shape three ways, so a test can ask what the *writer* does with a curve without
    also depending on which container it arrived in.
    """
    name = name or kind
    if kind == "BeamCurved":
        curve = exact_arc_spline()
        p1 = tuple(float(c) for c in curve.control_points_list[0])
        p2 = tuple(float(c) for c in curve.control_points_list[-1])
        return BeamCurved(name, p1, p2, curve, sec, mat="S355", **kwargs)
    if kind == "BeamRevolve":
        curve = CurveRevolve((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), radius=2.0, rot_axis=(0.0, 0.0, 1.0))
        return BeamRevolve(name, curve, sec, mat="S355", **kwargs)
    if kind == "BeamSweep":
        # A filleted corner. Note this is NOT a single curved leg: CurveOpen2d decomposes it
        # into line-arc-line, which the writer refuses -- see
        # test_a_swept_path_is_refused_for_having_several_legs. The class is still not refused
        # by type, which is what the shape guard's test uses this for.
        curve = CurveOpen2d(
            [(0.0, 0.0), (2.0, 0.0, 0.5), (2.0, 2.0)],
            origin=(0.0, 0.0, 0.0),
            xdir=(1.0, 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
        )
        return BeamSweep(name, curve, sec, mat="S355", **kwargs)
    raise AssertionError("no fixture for {0!r}".format(kind))


def a_curved_model(kind="BeamCurved", extra=(), part_name="Curves"):
    part = ada.Part(part_name)
    part.add_beam(a_curved_beam(kind))
    for bm in extra:
        part.add_beam(bm)
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


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
# Guard 2 — a beam's shape, by exact type: three curved classes built, the taper refused
# --------------------------------------------------------------------------------------


#: The ``Beam`` subclasses that must be refused, as **factories** rather than instances.
#:
#: This used to be a function called *inside* the ``parametrize`` decorator, so it ran at
#: import time -- which means a failure to construct any one of these objects killed
#: collection for the whole test session instead of failing one test. Every entry is built
#: lazily now, inside the test that needs it, and the dict key supplies the readable id.
REFUSED_BEAM_FACTORIES = {
    "BeamTapered": lambda: BeamTapered("tapered", (0, 0, 0), (0, 0, 3), "IPE300", "IPE200"),
}


@pytest.mark.parametrize("kind", sorted(REFUSED_BEAM_FACTORIES))
def test_a_refused_beam_subclass_is_refused_by_exact_type(kind):
    """``isinstance`` would let it through, which is why the test is exact-type.

    ``BeamTapered`` is the only one left of the original four, and it is not refused out of
    caution: CAE accepts ``beamShape=TAPERED`` and reads it back, and then writing the INP
    kills the kernel.
    """
    bm = REFUSED_BEAM_FACTORIES[kind]()
    assert isinstance(bm, ada.Beam), "if this fails the test has stopped testing anything"
    with pytest.raises(UnsupportedBeamError, match=kind):
        check_beam_shape(bm)


def test_the_tapered_refusal_cites_both_measurements_rather_than_a_preference():
    """The reason is two measurements, and the message has to carry both or it reads as timidity.

    CAE offers two ways to write a taper and Abaqus 2025 fails at both, differently, which is
    why one number is not enough here. ``integration=DURING_ANALYSIS`` segfaults the INP
    writer -- for PIPE, I, RECT, CIRC and BOX alike and for both ``B31`` and ``B32``, with the
    constant-section control writing, so the taper is provably the cause.
    ``BEFORE_ANALYSIS`` *does* write, a plausible ``Taper`` general section that solves, and
    its answer does not depend on which end is which: 9.122384e-02 / 8.756731e-02 /
    8.635153e-02 at 1, 2 and 40 elements in **both** directions, where a real taper differs by
    about 3x. A refusal that quoted only the segfault would invite the obvious "so write it
    BEFORE_ANALYSIS instead", which is the reading this message has to close off.
    """
    with pytest.raises(UnsupportedBeamError) as raised:
        check_beam_shape(REFUSED_BEAM_FACTORIES["BeamTapered"]())

    message = str(raised.value)
    # Fact one: the crash, its breadth, and the control that isolates the taper as its cause.
    assert "SEGMENTATION FAULT" in message
    assert "code 11 (0XB)" in message
    assert "straight_constant  -> WROTE OK" in message
    assert "DURING_ANALYSIS" in message
    assert "PIPE, I, RECT, CIRC, BOX" in message
    # Fact two: the deck that writes, solves, and answers the same whichever way round it is.
    assert "BEFORE_ANALYSIS" in message
    assert "INDEPENDENT OF WHICH END IS WHICH" in message
    for measured in ("9.122384e-02", "8.756731e-02", "8.635153e-02"):
        assert measured in message, "the taper's own numbers are what make the second fact a fact"
    assert "per-element sections" in message, "and what faithful tapering would actually need"


def test_a_straight_beam_is_not_refused():
    check_beam_shape(ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300"))


def test_a_beam_subclass_nobody_has_seen_is_still_refused():
    """The exactness of the type test is what makes a new subclass a refusal, not a chord."""

    class BeamSomethingNew(ada.Beam):
        pass

    with pytest.raises(UnsupportedBeamError, match="BeamSomethingNew"):
        check_beam_shape(BeamSomethingNew("odd", (0, 0, 0), (0, 0, 3), "IPE300"))


@pytest.mark.parametrize("kind", ["BeamCurved", "BeamRevolve", "BeamSweep"])
def test_the_curved_classes_are_no_longer_refused_by_type(kind):
    """All three carry the exact curve, so there is nothing to approximate on the way in.

    Whether a given member can be *drawn* is then a question about its curve rather than its
    class, and is refused separately and by name -- which is the point of splitting the two
    checks. ``BeamSweep`` is the case where that distinction bites: its class passes here and
    its usual multi-leg path does not pass the curve check.
    """
    check_beam_shape(a_curved_beam(kind))


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
# Guard 3 — a constant eccentricity becomes the section's own offset; the rest is refused
# --------------------------------------------------------------------------------------


def test_a_constant_offset_is_projected_onto_the_sections_own_axes():
    """The member runs along +z with ``n1 = yvec``; the offset is stated in ``(n1, n2)``.

    For a vertical member adapy gives ``up = (0, 1, 0)`` and ``yvec = (1, 0, 0)``, so
    ``n2 = t x n1 = (0, 1, 0)`` and a global offset of ``(0, 0.4, 0)`` reads as ``(0, 0.4)``:
    nothing along n1, all of it along n2. The literal pair is asserted as well as the
    projection formula, because a test that only re-derives the formula from the same two
    vectors the code used cannot tell a wrong frame from a right one.
    """
    bm = ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0))
    n1 = np.asarray(beam_n1(bm))
    axis = np.asarray([0.0, 0.0, 1.0])
    n2 = np.cross(axis, n1)

    offset = beam_section_offset(bm)

    assert offset == pytest.approx((float(np.dot((0.0, 0.4, 0.0), n1)), float(np.dot((0.0, 0.4, 0.0), n2))))
    assert offset == pytest.approx((0.0, 0.4))


def test_the_projection_keeps_the_offsets_length_and_loses_nothing():
    """A skew member, where neither component is zero and no axis is aligned with anything.

    This is the test that would catch a projection onto the wrong pair of vectors: any
    orthonormal pair reproduces the length, but only ``(yvec, t x yvec)`` reproduces the two
    components, and the residual perpendicular to both has to be zero or part of the offset
    was thrown away.
    """
    bm = ada.Beam("skew", (0, 0, 0), (6, 3, 4), "IPE300", e1=(0.11, -0.23, 0.07), e2=(0.11, -0.23, 0.07))
    start, end = beam_endpoints(bm)
    axis = np.asarray(end) - np.asarray(start)
    axis = axis / np.linalg.norm(axis)
    n1 = np.asarray(beam_n1(bm))
    n2 = np.cross(axis, n1)
    ecc = np.asarray([0.11, -0.23, 0.07])
    # A section offset has no axial component, so the member is chosen with the offset already
    # perpendicular to its axis -- which is what the writer insists on; see the test below.
    ecc = ecc - float(np.dot(ecc, axis)) * axis
    bm.e1 = tuple(ecc)
    bm.e2 = tuple(ecc)

    a1, a2 = beam_section_offset(bm)

    assert (a1 * a1 + a2 * a2) ** 0.5 == pytest.approx(float(np.linalg.norm(ecc)), rel=1e-12)
    assert np.allclose(a1 * n1 + a2 * n2, ecc, atol=1e-12)
    assert abs(a1) > 1e-3 and abs(a2) > 1e-3, "the fixture must exercise both components"


def test_an_explicit_zero_offset_is_not_an_offset():
    """adapy stores a ``Direction`` either way, so ``(0,0,0)`` must not put an offset on a section."""
    assert beam_section_offset(ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0, 0, 0), e2=(0, 0, 0))) is None


def test_no_offset_at_all_is_none():
    assert beam_section_offset(ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300")) is None


@pytest.mark.parametrize(
    "e1,e2",
    [
        ((0.0, 0.4, 0.0), (0.0, 0.2, 0.0)),
        ((0.0, 0.4, 0.0), None),
        (None, (0.0, 0.4, 0.0)),
    ],
)
def test_a_varying_offset_is_still_refused(e1, e2):
    """One 2-tuple cannot say 'this much at one end and that much at the other'.

    The ``None`` cases matter as much as the unequal pair: an offset at one end only is a
    varying offset, and treating a missing ``e2`` as "the same as e1" would move the far end
    of the member by the whole offset without a word.
    """
    kwargs = {}
    if e1 is not None:
        kwargs["e1"] = e1
    if e2 is not None:
        kwargs["e2"] = e2
    bm = ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300", **kwargs)
    with pytest.raises(UnsupportedBeamError, match="VARYING offset"):
        beam_section_offset(bm)


def test_an_axial_offset_is_refused_because_no_section_offset_can_express_it():
    """A section offset moves the cross-section sideways; it cannot move a member along itself.

    adapy's Genie reader strips the axial component for most offset containers precisely
    because it would otherwise extend a member past its landing wall -- an audit model has 280
    stiffeners like that. Whatever survives to here has to be refused rather than dropped.
    """
    bm = ada.Beam("bm", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0.0, 0.4, 0.25), e2=(0.0, 0.4, 0.25))
    with pytest.raises(UnsupportedBeamError, match="AXIAL component"):
        beam_section_offset(bm)


def arc_end_frames(bm):
    """``[(tangent, n1_proj, n2), ...]`` at a curved member's two ends, derived here.

    A second implementation of the frame :func:`curved_beam_section_offset` projects in, so a
    test can state the offset it means *in the frame the arc has* and then assert the literal
    pair the writer must return for it. Only the fixtures below are built this way; what each
    test asserts is the literal 2-tuple, which is the part a wrong frame would get wrong.
    """
    points = np.asarray(sample_member_curve(bm, *beam_endpoints(bm), 1e-04)[0])
    n1 = np.asarray(beam_n1(bm))
    frames = []
    for chord in (points[1] - points[0], points[-1] - points[-2]):
        tangent = chord / np.linalg.norm(chord)
        n1_proj = n1 - float(np.dot(n1, tangent)) * tangent
        n1_proj = n1_proj / np.linalg.norm(n1_proj)
        frames.append((tangent, n1_proj, np.cross(tangent, n1_proj)))
    return frames


def curved_offset(bm):
    """The writer's ``beamSectionOffset`` for a curved member, sampled the way it samples it."""
    path, _ = sample_member_curve(bm, *beam_endpoints(bm), 1e-04)
    return curved_beam_section_offset(bm, path)


def test_an_out_of_plane_offset_on_a_curved_member_is_carried_as_the_measured_pair():
    """The narrowing this replaces a blanket refusal with, on the number that was measured.

    Abaqus applies the 2-tuple in the local ``(n1, n2)`` frame element by element, and on a
    planar arc whose ``n1`` lies in that plane ``n2 = t x n1_proj`` is a constant ``+Z`` --
    so an offset out of the arc's plane is one constant pair and is **exact**. Measured on
    Abaqus 2025 for a quarter arc of radius 4 with ``beamSectionOffset=(0, 0.4)`` against the
    same arc drawn at ``z = +0.4`` and tied back with ``*MPC BEAM``: all six displacement
    components identical to every printed digit (``u2 = 6.210658699e-02``,
    ``ur3 = -1.614604890e-02``).

    This fixture is the same geometry at radius 2 and offset 0.2, so the pair must come out
    ``(0, +0.2)``: nothing along ``n1``, all of it along ``n2``, and positive -- the sign that
    was pinned by the solver for the straight member.
    """
    bm = a_curved_beam("BeamCurved", e1=(0.0, 0.0, 0.2), e2=(0.0, 0.0, 0.2))
    (_, _, n2_start), (_, _, n2_end) = arc_end_frames(bm)
    assert n2_start == pytest.approx((0.0, 0.0, 1.0), abs=1e-12), "the fixture must have a constant +Z n2"
    assert n2_end == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)

    assert curved_offset(bm) == pytest.approx((0.0, 0.2), abs=1e-12)


def test_an_in_plane_offset_on_a_curved_member_is_still_refused_and_quotes_both_ends():
    """The case the blanket refusal was right about, kept, and now with the numbers in it.

    A constant *radial* offset on a planar arc has ``e1 == e2`` in global axes, so no test on
    the global vectors can tell it from the out-of-plane offset above -- and it is a different
    offset at each end, because its ``n1_proj`` component reads -0.2 at one end of a quarter
    arc and -0.0022 at the other. One 2-tuple cannot be both, so it is refused, and the
    message quotes both projections rather than only saying no.
    """
    bm = a_curved_beam("BeamCurved", e1=(0.2, 0.0, 0.0), e2=(0.2, 0.0, 0.0))

    with pytest.raises(UnsupportedBeamError) as raised:
        curved_offset(bm)

    message = str(raised.value)
    assert "not one constant pair in its own section frame" in message
    assert "-0.19998" in message and "-0.0022" in message, "both projections have to be in the message"


def test_an_offset_that_differs_globally_but_not_in_the_frame_is_carried():
    """``e1 == e2`` is neither necessary nor sufficient, and this is the "not necessary" half.

    adapy's Genie reader resolves a curved member's eccentricity **per end** -- ``segs[0]``
    for ``e1`` and ``segs[-1]`` for ``e2`` -- so two different global vectors are the ordinary
    case on an arc, and they are the *same* offset whenever they are the same pair in each
    end's own frame. Here they differ by 0.42 length units globally and are ``(0.3, 0.2)`` at
    both ends, which is what the writer must emit.
    """
    bm = a_curved_beam("BeamCurved")
    (_, n1_start, n2_start), (_, n1_end, n2_end) = arc_end_frames(bm)
    bm.e1 = tuple(0.3 * n1_start + 0.2 * n2_start)
    bm.e2 = tuple(0.3 * n1_end + 0.2 * n2_end)
    globally = float(np.linalg.norm(np.asarray(bm.e1, dtype=float) - np.asarray(bm.e2, dtype=float)))
    assert globally > 0.4, "the fixture must exercise two genuinely different global vectors"

    assert curved_offset(bm) == pytest.approx((0.3, 0.2), abs=1e-12)


def test_an_offset_along_a_curved_members_own_tangent_is_refused_although_the_pair_agrees():
    """The "not sufficient" half, and the hole the pair comparison alone would leave.

    An offset along the tangent at each end projects to ``(0, 0)`` at both ends, so the
    comparison of the two pairs is perfectly happy with it -- and emitting ``(0, 0)`` would
    drop the whole offset in silence, which is the failure this writer exists to refuse. So
    the axial component is checked per end after the pair, and named.
    """
    bm = a_curved_beam("BeamCurved")
    (t_start, _, _), (t_end, _, _) = arc_end_frames(bm)
    bm.e1 = tuple(0.2 * t_start)
    bm.e2 = tuple(0.2 * t_end)

    with pytest.raises(UnsupportedBeamError, match="AXIAL component"):
        curved_offset(bm)


def test_a_curved_member_with_an_offset_it_cannot_carry_stops_the_whole_write(tmp_path):
    """The refusal is a plan-time one, like every other: no half-written script."""
    part = ada.Part("Arc")
    part.add_beam(a_curved_beam("BeamCurved", name="arc", e1=(0.2, 0.0, 0.0), e2=(0.2, 0.0, 0.0)))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    destination = tmp_path / "out.py"

    with pytest.raises(UnsupportedBeamError, match="not one constant pair"):
        assembly.to_abaqus_cae_script(destination)

    assert not destination.exists()


def test_a_varying_offset_stops_the_whole_write(tmp_path):
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0.0, 0.0, 0.66)))
    destination = tmp_path / "out.py"
    with pytest.raises(UnsupportedBeamError, match="0.66"):
        assembly.to_abaqus_cae_script(destination)
    assert not destination.exists()


def test_a_constant_offset_reaches_the_emitted_beam_section(tmp_path):
    _, text = emit(one_beam(e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0)), tmp_path)

    assert "beamSectionOffset=(0.0, 0.4)" in text
    assert "'sec_IPE300_S355_off_0_0p4': ((0.0, 0.4), 'beamSectionOffset')" in text
    # ... and the wire is still drawn between the nodes, not through the offset points: the
    # whole point of the section offset is that the geometry stays where the model has it.
    assert "points=(((0.0, 0.0, 0.0), (0.0, 0.0, 3.0)),)" in text


def test_a_model_with_no_offsets_emits_no_offset_argument_at_all(tmp_path):
    """A feature that changes the output of models it does not apply to is a feature nobody asked for."""
    _, text = emit(one_beam(), tmp_path)

    assert "beamSectionOffset" not in text
    assert "centroid=" not in text
    assert "SECTION_OFFSETS" not in text, "no offsets means no offset table and no offset guard"
    assert "_guard_section_offsets" not in text
    assert "CURVE_LENGTH_REL_TOL" not in text, "and no curves means no curve machinery either"
    assert "WireSpline" not in text


def test_a_generalized_section_carries_its_offset_as_centroid(tmp_path):
    """Measured: a BEFORE_ANALYSIS section REFUSES ``beamSectionOffset`` outright.

    ``BeamSection(..., beamSectionOffset=...)`` and ``setValues(beamSectionOffset=...)`` both
    raise ``TypeError: keyword error on beamSectionOffset``, although the attribute exists and
    reads back ``[0.0, 0.0]``. It takes ``centroid``, which CAE writes as ``*Centroid`` -- and
    the solver reproduced adapy's own ``*MPC BEAM`` route with it to every printed digit.

    A declared GENERAL section is now the only kind that lands here. A channel used to, and does
    not any more: it is an ``ArbitraryProfile``, which integrates DURING_ANALYSIS and takes the
    other keyword -- see :func:`test_an_arbitrary_section_carries_its_offset_as_beam_section_offset`.
    The refusal is only in this direction: that section kind treats ``centroid`` as an alias of
    ``beamSectionOffset`` rather than rejecting it, so the two are asymmetric and both need saying.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm", (0, 0, 0), (0, 0, 3), general_section(), e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0)))

    _, text = emit(assembly, tmp_path)

    assert "integration=BEFORE_ANALYSIS" in text
    assert "centroid=(0.0, 0.4)" in text
    assert "beamSectionOffset=" not in text
    assert "'centroid')" in text, "the guard has to read back the attribute that was written"


def test_an_arbitrary_section_carries_its_offset_as_beam_section_offset(tmp_path):
    """A channel's offset changed keyword when the channel changed profile class.

    Measured on Abaqus 2025, on a ``BeamSection(integration=DURING_ANALYSIS)`` holding an
    ``ArbitraryProfile``, one section per spelling and one exported INP per section:

    * ``beamSectionOffset=(0.0, 0.09)`` reads back as that and the INP gains
      ``*Beam Section Offset`` -- which is what this writer emits;
    * ``centroid=(0.0, 0.09)`` is the **same stored member**: it reads back out of
      ``beamSectionOffset`` and produces the identical ``*Beam Section Offset``. So the old spelling
      would not in fact have lost the offset here. It is still not the one written, because
      ``beamSectionOffset`` is the attribute this section kind documents and the attribute the guard
      can read back by name;
    * ``shearCenter=(0.0, 0.09)`` is accepted, leaves ``beamSectionOffset`` at ``(0.0, 0.0)``, and
      writes **nothing at all**. That is the argument on this section kind that fails in silence.

    The generalized section is the other way round and refuses ``beamSectionOffset`` outright --
    :func:`test_a_generalized_section_carries_its_offset_as_centroid`.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    channel = ada.Section("UNP200", from_str="UNP200x10")
    part.add_beam(ada.Beam("bm", (0, 0, 0), (0, 0, 3), channel, e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0)))

    _, text = emit(assembly, tmp_path)

    # The table verbatim, because the kernel does not check its shape: a five-float first row was
    # accepted in silence and read back padded with zeros, i.e. a different cross-section.
    assert (
        "model.ArbitraryProfile(name='UNP200', table=((0.07075, -0.09425, 0.0), "
        "(0.0, -0.09425, 0.0115), (0.0, 0.09425, 0.0085), (0.07075, 0.09425, 0.0115)))" in text
    )
    assert "integration=DURING_ANALYSIS" in text
    assert "beamSectionOffset=(0.0, 0.4)" in text
    assert "centroid=" not in text, "the generalized spelling belongs to generalized sections only"
    assert "shearCenter=" not in text, "which this section kind accepts and then ignores"
    assert "'beamSectionOffset')" in text, "the guard has to read back the attribute that was written"


def test_the_same_profile_at_two_offsets_is_two_sections(tmp_path):
    """The offset belongs to the section, so two offsets are two sections and not one.

    Sharing the section would give one of the two members the other's eccentricity, which is
    the silent coordinate error in a new place.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("a", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0)))
    part.add_beam(ada.Beam("b", (1, 0, 0), (1, 0, 3), "IPE300", e1=(0.0, 0.1, 0.0), e2=(0.0, 0.1, 0.0)))
    part.add_beam(ada.Beam("c", (2, 0, 0), (2, 0, 3), "IPE300"))

    plan = build_plan(assembly)

    assert [use.cae_section_name for use in plan.sections] == [
        "sec_IPE300_S355",
        "sec_IPE300_S355_off_0_0p1",
        "sec_IPE300_S355_off_0_0p4",
    ]
    assert [use.offset for use in plan.sections] == [None, (0.0, 0.1), (0.0, 0.4)]
    _, text = emit(assembly, tmp_path)
    assert text.count("model.IProfile(") == 1, "one profile, three sections"


def test_a_real_genie_model_with_offsets_writes(fem_files, tmp_path):
    """``beams_constant_offset.xml``, which used to raise ``UnsupportedBeamError``.

    Seven Genie members, six with a constant offset, among them a channel and an angle (whose
    offset is an explicit ``-0.0`` and must not be treated as an offset at all). Built for real in
    Abaqus 2025 from exactly this file: 7 edges, 14 vertices, every offset read back.

    The channel's offset used to be a ``*Centroid`` here, because a channel used to be a
    generalized section. It is an ``ArbitraryProfile`` now, so all six offsets take the one
    keyword. (``centroid`` would in fact have worked too -- measured, it is an alias of
    ``beamSectionOffset`` on a DURING_ANALYSIS section -- but it is not that section kind's name
    for it, and a guard can only read back a name.)
    """
    assembly = ada.from_genie_xml(fem_files / "sesam/varying_offset/beams_constant_offset.xml")

    _, text = emit(assembly, tmp_path)

    plan = build_plan(assembly)
    offsets = {use.cae_section_name: use.offset for use in plan.sections if use.offset is not None}
    assert len(offsets) == 6, "six of the seven members carry an offset"
    assert offsets["sec_UNP180_S355_off_0_0p09"] == pytest.approx((0.0, 0.09))
    assert "beamSectionOffset=(0.0, 0.09)" in text, "the channel is an ArbitraryProfile, so it takes this"
    assert text.count("beamSectionOffset=(") == 6
    assert "centroid=" not in text, "nothing in this model is a generalized section any more"
    # The angle's offset is an explicit (0, 0, -0.0), which is not an offset.
    assert "sec_HP180x10_S355'" in text
    assert "sec_HP180x10_S355_off" not in text


def test_every_offset_in_adapys_own_genie_corpus_is_constant(fem_files):
    """Which is the justification for refusing the varying case rather than implementing it.

    Stated as a test because it is the evidence, and because the day a fixture with a varying
    offset arrives is the day this has to be revisited -- better that it fails here, next to
    the reasoning, than that someone rediscovers the refusal from a stack trace.
    """
    constant = 0
    varying = 0
    for name in sorted(p.name for p in (fem_files / "sesam/varying_offset").glob("*.xml")):
        for bm in ada.from_genie_xml(fem_files / "sesam/varying_offset" / name).get_all_physical_objects(
            by_type=ada.Beam
        ):
            ends = [
                (0.0, 0.0, 0.0) if getattr(bm, label) is None else tuple(float(c) for c in getattr(bm, label))
                for label in ("e1", "e2")
            ]
            if max(abs(c) for end in ends for c in end) <= 1e-09:
                continue
            if max(abs(a - b) for a, b in zip(*ends)) > 1e-09:
                varying += 1
            else:
                constant += 1

    assert varying == 0, "a varying offset has arrived in the corpus; the refusal needs revisiting"
    assert constant == 8, "the fixtures changed; check what the offsets are now"


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


def test_unit_scale_is_refused_because_it_never_scaled_the_material(tmp_path):
    """This test used to assert ``h=300.0`` and ``t3=7.1``, and passed with the bug present.

    ``unit_scale=1000`` multiplied every coordinate and every profile dimension and left
    the *material* untouched, so the writer emitted ``IProfile(..., h=300.0)`` beside
    ``Elastic(table=((210000000000.0, 0.3),))`` — a millimetre model carrying a modulus in
    pascals, every stiffness in it out by 10⁶, and not one guard reporting it. The old
    assertions were green because they never looked at the material: that is the whole
    lesson, so the pair is asserted here instead of being deleted with the test.

    No single factor repairs it. ``E`` scales as s⁻² and density as s⁻³ *only after* a
    force/mass unit has been chosen — Abaqus in N/mm/s wants tonne/mm³, so m→mm scales
    density by 1e-12 and not by 1e-9. So the scale is refused and the conversion belongs
    to the model.
    """
    destination = tmp_path / "scaled.py"
    with pytest.raises(CaeWriteError, match="unit_scale=1000.0 is refused"):
        one_beam().to_abaqus_cae_script(destination, unit_scale=1000.0)
    assert not destination.exists(), "a refused scale must leave no half-written script"

    # The pair the old assertions never put side by side. At unit_scale=1.0 it is
    # consistent: metres in the profile, pascals in the material.
    _, text = emit(one_beam(), tmp_path)
    assert "h=0.3" in text
    assert "table=((210000000000.0, 0.3),)" in text
    assert "h=300.0" not in text


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


def millimetre_beam():
    """The same 3 m member, authored in millimetres.

    Since ``unit_scale`` is refused, a millimetre model is one whose *coordinates* are in
    millimetres — which is how a model converted upstream arrives, and the case the
    cylinder fractions have to survive.
    """
    part = ada.Part("P")
    part.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3000), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


def test_the_cylinder_is_a_fraction_of_the_member_not_an_absolute_length(tmp_path):
    """The same structure in millimetres must be located the same way.

    An absolute clamp here would be the obvious implementation and would be wrong in
    exactly this writer's characteristic way: 1 mm is a sane radius for a model in metres
    and a hundredth of a micron for the same model in millimetres.

    Note this is the *opposite* of the joint tolerance, which is absolute on purpose: CAE
    merges two wire points below 1e-6 model units at every part size measured (0.004, 4
    and 4000 long), so that one is a kernel constant while this one is a question about the
    model. The two must not be made to look alike.
    """
    _, in_metres = emit(one_beam(), tmp_path, name="m.py")
    _, in_millimetres = emit(millimetre_beam(), tmp_path, name="mm.py")
    assert "radius=0.0003)" in in_metres
    assert "radius=0.3)" in in_millimetres


@pytest.mark.parametrize("scale", [1000.0, 0.001, 2.0, 0.0, -1.0])
def test_no_scale_but_one_gets_through(scale, tmp_path):
    """Including the ones a linear factor could not express even in principle.

    A ``GeneralizedProfile``'s arguments are an area and second moments — L², L⁴, not L —
    so the old code refused *those* while happily mis-scaling everything else. The refusal
    is now at the top, where the rest of the model cannot slip past it.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    general = ada.Section("GEN", "GENERAL", genprops=GeneralProperties(Ax=0.01, Ix=1e-6, Iy=1e-5, Iz=1e-5))
    part.add_beam(ada.Beam("bm", (0, 0, 0), (0, 0, 3), general))
    with pytest.raises(CaeWriteError, match="is refused"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py", unit_scale=scale)


def test_the_emitted_script_no_longer_advertises_a_scale_it_cannot_apply(tmp_path):
    """A banner saying ``unit_scale: 1.0`` invites the next reader to try 1000.0."""
    _, text = emit(one_beam(), tmp_path)
    assert "unit_scale must be 1.0" in text
    assert "'unit_scale':" not in text, "the result sidecar should not carry a scale that is always 1"


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


def test_a_plate_is_listed_as_untranslated_when_the_caller_says_plates_false(tmp_path):
    """``plates=False`` is the pre-plate behaviour, and it has to stay reachable.

    It is the only way to write a model whose members lie on its plates, which cannot be
    expressed as one CAE part at all -- so the absence has to be visible, in the header and in
    the sidecar, exactly as every other untranslated object's is.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300"))
    part.add_plate(a_plate())
    _, text = emit(assembly, tmp_path, plates=False)
    assert "NOT translated by this writer" in text
    assert "Plate 'pl1'" in text
    assert '"name": "pl1"' in text  # and in the machine-readable result sidecar
    assert "model.Part(name='P'," in text  # the beams are still built
    assert "mdb.openAcis(" not in text  # and no ACIS body was imported
    assert "HomogeneousShellSection" not in text
    assert "PLATES = {" not in text


def test_a_part_with_no_beams_and_no_plates_becomes_no_cae_part(tmp_path):
    assembly = ada.Assembly("A")
    with_beams = assembly.add_part(ada.Part("Deck"))
    with_beams.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300"))
    assembly.add_part(ada.Part("Empty"))
    _, text = emit(assembly, tmp_path)
    assert text.count("model.Part(name=") == 1
    assert "'Empty'" not in text


def test_a_part_holding_only_plates_still_becomes_a_cae_part(tmp_path):
    """A plate part needs no beams to be worth building, and it is built by a different call.

    PartFromGeometryFile *creates* the part from the ACIS body, so ``model.Part(...)`` is not
    what a plate part is made with -- which is why "a part with no beams is no part" could not
    survive plates.
    """
    assembly = ada.Assembly("A")
    assembly.add_part(ada.Part("Deck")).add_plate(a_plate())
    _, text = emit(assembly, tmp_path)
    assert "model.Part(name=" not in text
    assert "model.PartFromGeometryFile(name='Deck'" in text
    assert "mdb.openAcis(_beside_script('out_Deck.sat'), scaleFromFile=OFF)" in text


def test_a_model_with_neither_beams_nor_plates_is_refused(tmp_path):
    assembly = ada.Assembly("A")
    assembly.add_part(ada.Part("P"))
    with pytest.raises(CaeWriteError, match="holds no beams and no plates"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


def test_a_model_of_plates_alone_is_refused_when_plates_are_switched_off(tmp_path):
    assembly = ada.Assembly("A")
    assembly.add_part(ada.Part("P")).add_plate(a_plate())
    with pytest.raises(CaeWriteError, match="holds no beams and no plates"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py", plates=False)


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
    """``faces`` is what tells a plate boundary edge from a wire: measured on Abaqus 2025,
    ``edge.getFaces()`` is empty for a wire and holds the face indices for a boundary."""

    def __init__(self, index, faces=()):
        self.index = index
        self.pointOn = ((float(index), 0.0, 0.0),)
        self._faces = tuple(faces)

    def getFaces(self):
        return self._faces


class FakeFace:
    def __init__(self, index, area=1.0, normal=(0.0, 0.0, 1.0)):
        self.index = index
        self.pointOn = ((float(index), 0.5, 0.0),)
        self._area = area
        self._normal = normal

    def getSize(self, printResults=True):
        return self._area

    def getNormal(self, point=None):
        return self._normal


class FakeSet:
    def __init__(self, edges, faces=()):
        self.edges = edges
        self.faces = tuple(faces)


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

    ``sets`` maps a set name to the EDGE indices it holds, and ``face_sets`` to the FACE
    indices -- two mappings rather than one, because the guard now has two clauses and an
    assignment covering faces takes no beam orientation. ``boundary_edges`` names the edges
    that bound a face, which are the ones legitimately carrying no section.
    """

    def __init__(self, edge_count, sets, vertices=(), faces=0, face_sets=None, boundary_edges=(), face_areas=None):
        boundary = set(boundary_edges)
        self.edges = [FakeEdge(i, faces=(0,) if i in boundary else ()) for i in range(edge_count)]
        areas = face_areas or {}
        self.faces = [FakeFace(i, area=areas.get(i, 1.0)) for i in range(faces)]
        face_sets = face_sets or {}
        self.sets = {name: FakeSet([self.edges[i] for i in indices]) for name, indices in sets.items()}
        for name, indices in face_sets.items():
            self.sets[name] = FakeSet([], faces=[self.faces[i] for i in indices])
        self.sectionAssignments = [FakeAssignment(name) for name in sorted(self.sets)]
        # Only the EDGE assignments get an orientation, which is what CAE does: a shell
        # section takes none, so counting every assignment would fail every plate model.
        self.beamSectionOrientations = [FakeAssignment(name) for name in sorted(sets)]
        self.vertices = [FakeVertex(p) for p in vertices]


class FakeModel:
    """Only what the guards read: parts, and every name space guard 7 checks.

    ``steps`` defaults to ``{"Initial": ...}`` rather than to empty, because that is what a real
    CAE model already holds before a script creates anything -- and a step in the source model
    named ``Initial`` is exactly the collision guard 7 has to catch there.
    """

    def __init__(
        self,
        parts,
        materials=None,
        profiles=None,
        sections=None,
        instances=None,
        steps=None,
        boundary_conditions=None,
        loads=None,
        assembly_sets=None,
        assembly_surfaces=None,
        field_output_requests=None,
    ):
        self.parts = parts
        self.materials = materials if materials is not None else {}
        self.profiles = profiles if profiles is not None else {}
        self.sections = sections if sections is not None else {}
        self.steps = {"Initial": object()} if steps is None else steps
        self.boundaryConditions = boundary_conditions if boundary_conditions is not None else {}
        self.loads = loads if loads is not None else {}
        self.fieldOutputRequests = field_output_requests if field_output_requests is not None else {}
        self.rootAssembly = types.SimpleNamespace(
            instances=instances if instances is not None else {},
            sets=assembly_sets if assembly_sets is not None else {},
            # A CAE Pressure's region is a Surface, which is its own assembly repository -- so guard
            # 7 checks that namespace too and the stand-in has to have one.
            surfaces=assembly_surfaces if assembly_surfaces is not None else {},
        )


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
    assert "has no edges and no faces at all" in result["errors"][0]


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
    assert result["schema"] == "ada.cae_build_result/5", "plates added 'plates', 'pressure_faces' and four guard keys"


def test_the_sidecar_name_follows_the_script_stem(tmp_path):
    _, text = emit(one_beam(), tmp_path, name="jacket_rev32.py")
    assert "RESULT_NAME = 'jacket_rev32.cae_build_result.json'" in text
    assert "CAE_NAME = 'jacket_rev32.cae'" in text


def test_the_writer_returns_only_what_it_wrote(tmp_path):
    written, _ = emit(one_beam(), tmp_path)
    assert written == [tmp_path / "out.py"]
    assert all(isinstance(p, pathlib.Path) for p in written)


# --------------------------------------------------------------------------------------
# Guard 6 — the expected topology, which is the only thing that can see connectivity
# --------------------------------------------------------------------------------------


def stacked():
    """Two collinear columns, end to end. Measured in CAE: 2 edges, 3 vertices."""
    part = ada.Part("Stack")
    part.add_beam(ada.Beam("col_lower", (0, 0, 0), (0, 0, 4), "IPE300"))
    part.add_beam(ada.Beam("col_upper", (0, 0, 4), (0, 0, 8), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


def two_members_clear_of_each_other():
    part = ada.Part("Pair")
    part.add_beam(ada.Beam("a", (0, 0, 0), (4, 0, 0), "IPE300"))
    part.add_beam(ada.Beam("b", (2, 2, 0), (2, 4, 0), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


def test_the_expected_topology_is_what_the_kernel_actually_builds():
    """Built for real in Abaqus 2025: this frame gives ``edges=6 vertices=7``.

    The girder is two sub-edges because the brace lands on its middle and CAE imprints the
    landing into a split; everything else is one. ``skew`` starts on ``col1``'s own end
    node, which is a shared vertex and splits nothing.
    """
    topology = build_plan(frame()).parts[0].topology

    assert topology.edges_per_member == {"brace": 1, "col1": 1, "col2": 1, "girder": 2, "skew": 1}
    assert topology.edges == 6
    assert topology.vertices == 7


def test_a_landing_member_splits_the_through_member_and_not_itself():
    """The asymmetry is the whole content of the arithmetic: one of the two is split."""
    topology = build_plan(frame()).parts[0].topology

    assert topology.edges_per_member["girder"] == 2
    assert topology.edges_per_member["brace"] == 1
    assert topology.splits["girder"] == ((3.0, 0.0, 4.0),)
    assert topology.splits["brace"] == ()


def test_two_members_meeting_end_to_end_split_nothing():
    """Stacked columns are collinear and touch; measured in CAE as 2 edges and 3 vertices.

    If "inside" were tested inclusively this would read 2 sub-edges each, and every frame
    corner in every model would be reported as a failure.
    """
    topology = build_plan(stacked()).parts[0].topology

    assert topology.edges_per_member == {"col_lower": 1, "col_upper": 1}
    assert topology.vertices == 3


def test_two_braces_landing_on_the_same_point_split_the_girder_once():
    """Measured in CAE: 4 edges and 5 vertices, not 5 and 5."""
    part = ada.Part("P")
    part.add_beam(ada.Beam("girder", (0, 0, 0), (4, 0, 0), "IPE300"))
    part.add_beam(ada.Beam("up", (2, 0, 0), (2, 0, 2), "IPE300"))
    part.add_beam(ada.Beam("out", (2, 0, 0), (2, 2, 0), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    topology = build_plan(assembly).parts[0].topology

    assert topology.edges_per_member == {"girder": 2, "out": 1, "up": 1}
    assert topology.edges == 4
    assert topology.vertices == 5


def test_a_joint_adapy_calls_a_joint_splits_even_when_cae_will_not_merge_it():
    """A brace 1e-6 off the girder's line. This is the case the guard exists to report.

    Measured on Abaqus 2025: CAE merges two wire points below 1e-6 model units and not at
    1e-6 (9e-7 merges, 1e-6 does not), absolutely and at every part size probed. adapy's
    own ``point_tol`` is 1e-4 — the distance at which its FEM node container treats two
    points as one node — so this brace *is* joined to the girder as far as adapy and the
    INP route are concerned, and will be loose in CAE. Predicting the split here is what
    makes the emitted script fail and name the member, instead of shipping a model with an
    inert brace hanging a micron off its girder.
    """
    part = ada.Part("P")
    part.add_beam(ada.Beam("girder", (0, 0, 4), (6, 0, 4), "IPE300"))
    part.add_beam(ada.Beam("brace", (3, 1e-06, 4), (3, 3, 4), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    topology = build_plan(assembly).parts[0].topology

    assert topology.edges_per_member["girder"] == 2


def test_a_member_further_off_than_point_tol_is_not_a_joint_at_all():
    """A millimetre clear of the girder is a gap in the model, and the writer copies it.

    Neither adapy nor CAE calls this a joint, so nothing is expected and nothing is
    reported. A guard that snapped it would be inventing geometry.
    """
    part = ada.Part("P")
    part.add_beam(ada.Beam("girder", (0, 0, 4), (6, 0, 4), "IPE300"))
    part.add_beam(ada.Beam("brace", (3, 0.001, 4), (3, 3, 4), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    topology = build_plan(assembly).parts[0].topology

    assert topology.edges_per_member == {"brace": 1, "girder": 1}
    assert topology.vertices == 4


def test_the_joint_tolerance_is_adapys_own_point_tol_and_not_a_number_of_its_own(monkeypatch):
    """Which is the justification for the value, so it has to be read and not copied.

    ``general_point_tol`` is what adapy's node container merges nodes at and what
    ``Connections.find`` looks for joints with. Hardcoding 1e-4 here would silently stop
    tracking it the first time a user tightened theirs.

    ``Config`` is a process-wide singleton whose ``reload_config`` *merges* the environment
    rather than resetting to it, so dropping the variable is not enough to put it back: the
    default has to be written in explicitly before the variable goes. Otherwise this test
    leaks a tenfold tolerance into every test that runs after it.
    """
    from ada.config import Config

    part = ada.Part("P")
    part.add_beam(ada.Beam("girder", (0, 0, 4), (6, 0, 4), "IPE300"))
    part.add_beam(ada.Beam("brace", (3, 0.001, 4), (3, 3, 4), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    assert build_plan(assembly).parts[0].topology.edges_per_member["girder"] == 1

    monkeypatch.setenv("ADA_GENERAL_POINT_TOL", "0.01")
    Config().reload_config()
    try:
        assert Config().general_point_tol == 0.01, "the fixture did not actually change the config"
        plan = build_plan(assembly)
        assert plan.joint_tol == 0.01
        assert plan.parts[0].topology.edges_per_member["girder"] == 2
    finally:
        monkeypatch.setenv("ADA_GENERAL_POINT_TOL", "0.0001")
        Config().reload_config()
        monkeypatch.undo()
    assert Config().general_point_tol == 1e-04
    assert build_plan(assembly).parts[0].topology.edges_per_member["girder"] == 1


def test_the_emitted_script_states_the_topology_and_both_tolerances(tmp_path, monkeypatch):
    _, text = emit(frame(), tmp_path)

    namespace, _ = load_emitted_script(text, tmp_path, monkeypatch)

    assert namespace["EXPECTED_TOPOLOGY"] == {
        "Frame": {
            "edges": 6,
            # Equal to 'edges' on a beams-only part: every member is drawn as a wire. They differ
            # once a member lies on a plate and becomes a Stringer on an edge the body carries.
            "wire_edges": 6,
            "vertices": 7,
            # A beams-only part: zero faces is what tells the guard to check the edge and
            # vertex totals rather than the faces and the face-free edges.
            "faces": 0,
            "stringers": [],
            "edges_per_member": {"brace": 1, "col1": 1, "col2": 1, "girder": 2, "skew": 1},
        }
    }
    assert namespace["JOINT_TOL"] == 1e-04
    # Measured, and stated in the script so a failure message can explain the band.
    assert namespace["CAE_MERGE_TOL"] == 1e-06


#: Seven distinct positions, which is what `frame()` must build.
SEVEN_POINTS = [(float(i), 0.0, 0.0) for i in range(7)]


def five_loose_sticks():
    """What ``mergeType=SEPARATE`` would leave: the frame, unmerged. 5 edges, 10 vertices."""
    return FakePart(
        5,
        {"brace": [0], "col1": [1], "col2": [2], "girder": [3], "skew": [4]},
        vertices=[(float(i), 0.0, 0.0) for i in range(10)],
    )


def test_guard_one_is_blind_to_exactly_what_guard_six_catches(tmp_path, monkeypatch):
    """The review finding, as a test: guard 1 passes on a disconnected frame.

    Measured with this writer's own wire calls::

        T-joint, IMPRINT (correct)        edges=3 vertices=4     guard 1 passes
        T-joint, SEPARATE (disconnected)  edges=2 vertices=4     guard 1 PASSES

    Every edge of the loose version carries exactly one section, because each member's own
    cylinder finds its own unsplit wire. So the guard that counts sections reports a clean
    build, and only the expected topology sees that the frame is a pile of sticks.
    """
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    namespace["_RESULT"]["edges_per_member"].update({"brace": 1, "col1": 1, "col2": 1, "girder": 1, "skew": 1})
    model = FakeModel({"Frame": five_loose_sticks()})

    namespace["_guard_every_edge_sectioned"](model)
    assert exits == [], "guard 1 is supposed to be fooled here; if it is not, say so and simplify"

    namespace["_guard_topology"](model)

    assert exits == [1]
    error = json.loads((tmp_path / "out.cae_build_result.json").read_text())["errors"][0]
    assert "member 'girder' expected 2 sub-edge(s), CAE built 1" in error
    assert "adapy described 6 edge(s), CAE built 5" in error
    assert "adapy described 7 vertex(es), CAE built 10" in error
    assert "a joint did not merge" in error


def test_the_topology_guard_catches_a_crossing_cae_imprinted_on_its_own(tmp_path, monkeypatch):
    """The backstop for a crossing the planner's refusal did not see.

    Measured: an X of two members builds ``edges=4 vertices=5`` where two members clear of
    each other build 2 and 4. Every one of those four edges gets a section, so guard 1 is
    silent again; the extra sub-edges are the only trace.
    """
    _, text = emit(two_members_clear_of_each_other(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["edges_per_member"].update({"a": 2, "b": 2})
    crossed = FakePart(4, {"a": [0, 1], "b": [2, 3]}, vertices=[(float(i), 0.0, 0.0) for i in range(5)])

    namespace["_guard_topology"](FakeModel({"Pair": crossed}))

    assert exits == [1]
    error = json.loads((tmp_path / "out.cae_build_result.json").read_text())["errors"][0]
    assert "member 'a' expected 1 sub-edge(s), CAE built 2" in error
    assert "member 'b' expected 1 sub-edge(s), CAE built 2" in error
    assert "CAE imprinted a connection adapy does not model" in error


def test_the_topology_guard_catches_the_joint_that_only_the_vertex_count_shows(tmp_path, monkeypatch):
    """Two collinear members that failed to join keep the same *edge* count.

    Measured: touching gives ``edges=2 vertices=3``, and 1e-6 apart gives
    ``edges=2 vertices=4``. Nothing but the vertex count moves — not the edge total, not
    any member's sub-edge count, not the section coverage — so without it this failure is
    invisible to every other guard in the script.
    """
    _, text = emit(stacked(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["edges_per_member"].update({"col_lower": 1, "col_upper": 1})
    apart = FakePart(2, {"col_lower": [0], "col_upper": [1]}, vertices=[(0.0, 0.0, float(i)) for i in range(4)])

    namespace["_guard_topology"](FakeModel({"Stack": apart}))

    assert exits == [1]
    error = json.loads((tmp_path / "out.cae_build_result.json").read_text())["errors"][0]
    assert "adapy described 3 vertex(es), CAE built 4" in error
    assert "sub-edge(s), CAE built" not in error, "no member's sub-edge count changed here"


def test_the_topology_guard_passes_on_the_topology_adapy_described(tmp_path, monkeypatch):
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["edges_per_member"].update({"brace": 1, "col1": 1, "col2": 1, "girder": 2, "skew": 1})
    built = FakePart(6, {"girder": [0, 1]}, vertices=[(float(i), 0.0, 0.0) for i in range(7)])

    namespace["_guard_topology"](FakeModel({"Frame": built}))

    assert exits == []
    assert namespace["_RESULT"]["guards"]["Frame"]["expected_vertices"] == 7


def test_the_sidecar_keeps_both_verdicts_and_not_just_the_later_one(tmp_path, monkeypatch):
    """Guard 1 records into the same per-part dict, and ran second.

    An assignment there would silently drop the connectivity verdict from the sidecar --
    leaving ``edges: 6`` with nothing to compare it against, which is the state this whole
    change exists to get out of.
    """
    _, text = emit(frame(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_RESULT"]["created"]["parts"].append("Frame")
    namespace["_RESULT"]["edges_per_member"].update({"brace": 1, "col1": 1, "col2": 1, "girder": 2, "skew": 1})
    built = FakePart(6, {"girder": [0, 1], "col1": [2], "col2": [3], "brace": [4], "skew": [5]}, vertices=SEVEN_POINTS)
    model = FakeModel({"Frame": built})

    namespace["_guard_topology"](model)
    namespace["_guard_every_edge_sectioned"](model)

    assert exits == []
    recorded = namespace["_RESULT"]["guards"]["Frame"]
    assert recorded["expected_edges"] == 6
    assert recorded["expected_vertices"] == 7
    assert recorded["edges"] == 6
    assert recorded["edges_with_no_section"] == 0


def test_the_topology_guard_runs_before_the_one_that_cannot_see_connectivity(tmp_path):
    """Order matters only for the message: the connectivity verdict is the useful one."""
    _, text = emit(one_beam(), tmp_path)
    body = text.split("def main():", 1)[1]

    assert body.index("_guard_topology(model)") < body.index("_guard_every_edge_sectioned(model)")


# --------------------------------------------------------------------------------------
# The crossing policy — refused while planning, never approximated
# --------------------------------------------------------------------------------------


def crossing_pair():
    part = ada.Part("X")
    part.add_beam(ada.Beam("girder", (0, 0, 0), (4, 0, 0), "IPE300"))
    part.add_beam(ada.Beam("brace", (2, -1, 0), (2, 1, 0), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


def test_two_members_crossing_away_from_their_ends_are_refused(tmp_path):
    """CAE welds a crossing exactly as it welds a real joint, and adapy models no joint here.

    Measured: an X of two 4 m members builds ``edges=4 vertices=5`` — a shared vertex, and
    therefore a moment connection, at a point the source model does not join. Asserting
    that instead of refusing it would make the change *known* without making it *right*,
    and ``mergeType`` is per-wire rather than per-pair, so "join the real joints but not
    this crossing" cannot be said in one CAE part at all.
    """
    destination = tmp_path / "crossed.py"

    with pytest.raises(CaeWriteError) as raised:
        crossing_pair().to_abaqus_cae_script(destination)

    message = str(raised.value)
    assert "'brace' and 'girder' cross at about (2, 0, 0)" in message
    assert "structural connectivity the source never had" in message
    assert not destination.exists(), "a refused model must leave no half-written script"


def test_a_brace_landing_on_a_girder_is_not_a_crossing(tmp_path):
    """The ordinary frame corner and the ordinary stacked column. Both must still write."""
    frame().to_abaqus_cae_script(tmp_path / "ok.py")
    stacked().to_abaqus_cae_script(tmp_path / "ok2.py")


def test_collinear_members_that_share_a_length_are_refused(tmp_path):
    """Measured: two collinear 4 m members overlapping by 2 build ``edges=3``.

    Counting one split per intruding endpoint would predict 4, and there is no honest
    answer to "which sub-edges belong to which member" over the shared stretch, so this is
    refused rather than mis-predicted. A duplicated member is the same case at full overlap.
    """
    part = ada.Part("Overlap")
    part.add_beam(ada.Beam("lower", (0, 0, 0), (4, 0, 0), "IPE300"))
    part.add_beam(ada.Beam("upper", (2, 0, 0), (6, 0, 0), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    with pytest.raises(CaeWriteError, match="collinear and overlap"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


def test_a_duplicated_member_is_refused_as_an_overlap(tmp_path):
    part = ada.Part("Twice")
    part.add_beam(ada.Beam("a", (0, 0, 0), (4, 0, 0), "IPE300"))
    part.add_beam(ada.Beam("b", (0, 0, 0), (4, 0, 0), "IPE300"))
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    with pytest.raises(CaeWriteError, match="collinear and overlap"):
        assembly.to_abaqus_cae_script(tmp_path / "out.py")


def test_members_in_different_parts_cannot_cross(tmp_path):
    """Separate CAE parts hold separate geometry, so the refusal is per part and must stay so."""
    assembly = ada.Assembly("A")
    first = assembly.add_part(ada.Part("One"))
    first.add_beam(ada.Beam("girder", (0, 0, 0), (4, 0, 0), "IPE300"))
    second = assembly.add_part(ada.Part("Two"))
    second.add_beam(ada.Beam("brace", (2, -1, 0), (2, 1, 0), "IPE300"))

    plan = build_plan(assembly)

    assert [p.topology.edges_per_member for p in plan.parts] == [{"girder": 1}, {"brace": 1}]


# --------------------------------------------------------------------------------------
# Guard 7 — CAE replaces a reused name instead of refusing it
# --------------------------------------------------------------------------------------


def test_the_script_lists_every_name_it_will_create(tmp_path, monkeypatch):
    _, text = emit(frame(), tmp_path)

    namespace, _ = load_emitted_script(text, tmp_path, monkeypatch)

    planned = namespace["PLANNED_NAMES"]
    assert planned["parts"] == ["Frame"]
    assert planned["instances"] == ["Frame-1"]
    assert planned["materials"] == ["S355"]
    assert planned["profiles"] == ["HEA300", "IPE300", "TUB200x10", "UNP200"]
    assert sorted(planned["sections"]) == planned["sections"]
    # Sets are deliberately absent: they live inside a part this script just created.
    assert "sets" not in planned


def test_the_collision_guard_lets_an_empty_model_through(tmp_path, monkeypatch):
    _, text = emit(one_beam(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)

    namespace["_guard_no_name_collisions"](FakeModel({}))

    assert exits == []


@pytest.mark.parametrize(
    "kind,existing",
    [
        ("parts", {"parts": {"P": object()}}),
        ("materials", {"materials": {"S355": object()}}),
        ("profiles", {"profiles": {"IPE300": object()}}),
        ("sections", {"sections": {"sec_IPE300_S355": object()}}),
        ("instances", {"instances": {"P-1": object()}}),
    ],
)
def test_the_collision_guard_refuses_a_second_run_into_the_same_model(kind, existing, tmp_path, monkeypatch):
    """Measured: CAE does not raise on a reused name. It replaces the object.

    So a second run in one GUI session would quietly swap every part, and the run would
    look as clean as the first. Every namespace is checked, because any one of them left
    out is a namespace that gets silently replaced.
    """
    _, text = emit(one_beam(), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    model = FakeModel(existing.pop("parts", {}), **existing)

    namespace["_guard_no_name_collisions"](model)

    assert exits == [1], "namespace {!r} is not checked".format(kind)
    error = json.loads((tmp_path / "out.cae_build_result.json").read_text())["errors"][0]
    assert "already holds 1 of the object(s)" in error
    assert kind in error
    assert "CAE replaces an object whose name is reused" in error


def test_the_collision_guard_runs_before_anything_is_built(tmp_path):
    """Once the first Part has been replaced there is nothing left to abort back to."""
    _, text = emit(one_beam(), tmp_path)
    body = text.split("def main():", 1)[1]

    assert body.index("_guard_no_name_collisions(model)") < body.index("build(model)")


# --------------------------------------------------------------------------------------
# Curved members: the wire, the sampling, and what the topology guard can still assert
# --------------------------------------------------------------------------------------


QUARTER_ARC_LENGTH = 2.0 * math.pi / 2.0  #: radius 2, sweep pi/2


#: The two classes adapy's Genie reader actually produces, and the two whose path is a
#: single curved leg. ``BeamSweep``'s is not: see test_a_swept_path_is_refused_for_having
#: _several_legs.
SAMPLED_CURVED_KINDS = {"BeamCurved": (0.0, 0.0, 0.0), "BeamRevolve": (2.0, 2.0, 0.0)}


@pytest.mark.parametrize("kind", sorted(SAMPLED_CURVED_KINDS))
def test_a_curved_member_is_sampled_on_its_own_exact_curve(kind):
    """Each class carries the curve; neither needs fitting.

    The samples have to lie *on* the curve, not near it, which for these two fixtures -- the
    same quarter circle of radius 2 reached two different ways -- means a constant radius
    about the arc's own centre, to the last bit a float carries.
    """
    bm = a_curved_beam(kind)
    p1, p2 = beam_endpoints(bm)

    points, arc_length = sample_member_curve(bm, p1, p2, 1e-04)

    assert points[0] == pytest.approx(p1) and points[-1] == pytest.approx(p2)
    assert len(points) >= 9
    radii = np.linalg.norm(np.asarray(points) - np.asarray(SAMPLED_CURVED_KINDS[kind]), axis=1)
    assert radii == pytest.approx(2.0, abs=1e-12), "every sample must sit on the arc, not near it"
    assert arc_length == pytest.approx(QUARTER_ARC_LENGTH, rel=1e-09)


def test_the_arc_length_is_the_curves_and_not_the_sum_of_the_chords():
    """The bug this test exists for was live and sat one hair inside its own tolerance.

    A chord polyline is systematically *short*: at the 33 points a quarter circle gets here it
    is 1.0e-04 of the arc short, which is forty times the spline's own error -- so a guard
    that compared CAE's spline against the chord sum would have been measuring adapy's
    sampling and would have read 9.78e-05 against a 1.0e-04 tolerance for no reason. It did,
    until the arc length was Richardson-extrapolated instead.
    """
    bm = a_curved_beam("BeamCurved")
    p1, p2 = beam_endpoints(bm)

    points, arc_length = sample_member_curve(bm, p1, p2, 1e-04)

    chords = float(np.linalg.norm(np.diff(np.asarray(points), axis=0), axis=1).sum())
    assert arc_length == pytest.approx(QUARTER_ARC_LENGTH, rel=1e-09)
    assert chords < arc_length, "the chord sum must be short of the arc, or this test proves nothing"
    assert abs(chords - QUARTER_ARC_LENGTH) / QUARTER_ARC_LENGTH > 1e-05, (
        "the chord deficit has to be big enough to matter against CURVE_LENGTH_REL_TOL, "
        "or the distinction this test defends is academic"
    )


@pytest.mark.parametrize("sweep", [math.pi / 8.0, math.pi / 2.0])
def test_the_sample_spacing_is_a_turning_angle_and_not_a_point_count(sweep):
    """A gentle arc gets fewer points than a tight one, in metres and in millimetres alike.

    A fixed count would over-sample one and under-sample the other, and an absolute chord
    tolerance would change behaviour with the model's units -- the mistake the cylinder
    fractions exist to avoid, in a new place.
    """
    curve = exact_arc_spline(radius=2.0, start=0.0, end=sweep)
    p1 = tuple(float(c) for c in curve.control_points_list[0])
    p2 = tuple(float(c) for c in curve.control_points_list[-1])
    bm = BeamCurved("arc", p1, p2, curve, "IPE300", mat="S355")

    points, _ = sample_member_curve(bm, *beam_endpoints(bm), 1e-04)

    chords = np.diff(np.asarray(points), axis=0)
    units = chords / np.linalg.norm(chords, axis=1)[:, None]
    turns = np.arccos(np.clip(np.einsum("ij,ij->i", units[:-1], units[1:]), -1.0, 1.0))
    assert turns.max() <= MAX_TURN_RADIANS
    # and not wastefully far inside it: halving the count would break the criterion
    assert turns.max() > MAX_TURN_RADIANS / 2.5


def test_the_same_arc_in_millimetres_is_sampled_at_the_same_points_scaled():
    """The turning-angle criterion is dimensionless, so a unit change must not change it."""
    fine = a_curved_beam("BeamCurved")
    big_curve = exact_arc_spline(radius=2000.0)
    big = BeamCurved(
        "arc_mm",
        tuple(float(c) for c in big_curve.control_points_list[0]),
        tuple(float(c) for c in big_curve.control_points_list[-1]),
        big_curve,
        "IPE300",
        mat="S355",
    )

    metres, _ = sample_member_curve(fine, *beam_endpoints(fine), 1e-04)
    millimetres, _ = sample_member_curve(big, *beam_endpoints(big), 1e-01)

    assert len(metres) == len(millimetres)
    assert np.allclose(np.asarray(millimetres) / 1000.0, np.asarray(metres), atol=1e-09)


def test_a_curve_whose_ends_are_not_the_members_nodes_is_refused():
    """A curve that does not start where the member starts is not that member's axis.

    Snapping it into place would move the geometry to fit the bookkeeping, which is the one
    thing this writer never does -- and the ends are what every joint in the part is computed
    from, so a curve 4 units adrift would silently build a different structure.
    """
    curve = exact_arc_spline()
    bm = BeamCurved("adrift", (0.0, 0.0, 0.0), (0.0, 2.0, 0.0), curve, "IPE300", mat="S355")

    with pytest.raises(CurveNotSupported, match="not this member's axis"):
        sample_member_curve(bm, *beam_endpoints(bm), 1e-04)


def test_a_curve_parameterised_backwards_is_reversed_rather_than_refused():
    """Which way round a curve runs says nothing about the member."""
    curve = exact_arc_spline()
    p1 = tuple(float(c) for c in curve.control_points_list[0])
    p2 = tuple(float(c) for c in curve.control_points_list[-1])
    forwards = BeamCurved("f", p1, p2, curve, "IPE300", mat="S355")
    backwards = BeamCurved("b", p2, p1, curve, "IPE300", mat="S355")

    ahead, length_ahead = sample_member_curve(forwards, *beam_endpoints(forwards), 1e-04)
    behind, length_behind = sample_member_curve(backwards, *beam_endpoints(backwards), 1e-04)

    assert behind == tuple(reversed(ahead))
    assert length_behind == pytest.approx(length_ahead, rel=1e-12)


def test_a_beamcurved_with_no_curve_is_refused_and_names_the_beam():
    bm = BeamCurved("nothing", (0, 0, 0), (0, 0, 3), None, "IPE300", mat="S355")
    part = ada.Part("P")
    part.add_beam(bm)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    with pytest.raises(UnsupportedBeamError, match="'nothing'"):
        build_plan(assembly)


def test_a_straight_curve_container_is_refused_rather_than_splined():
    """A spline through a polyline's corners rounds them off, which is a silent change of shape."""
    curve = gc.PolyLine(points=[(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 0.0, 0.0)])
    bm = BeamCurved("kinked", (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), curve, "IPE300", mat="S355")

    with pytest.raises(CurveNotSupported, match="piecewise straight"):
        sample_member_curve(bm, *beam_endpoints(bm), 1e-04)


def a_swept_beam(points, name="sweep", sec="IPE300", **kwargs):
    """One ``BeamSweep`` over a ``CurveOpen2d`` in the XY plane. A third coordinate is a fillet radius."""
    curve = CurveOpen2d(points, origin=(0.0, 0.0, 0.0), xdir=(1.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0))
    return BeamSweep(name, curve, sec, mat="S355", **kwargs)


def _swept_model(points, name, extra=()):
    part = ada.Part("Swept")
    part.add_beam(a_swept_beam(points, name=name))
    for bm in extra:
        part.add_beam(bm)
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    return assembly


class _StubSegment:
    """The three attributes the sweep sampler reads off one leg of a path."""

    def __init__(self, p1, p2):
        self.p1 = p1
        self.p2 = p2
        self.midpoint = None


def _stub_sweep(points3d, spans):
    """A stand-in swept member whose path is whatever shape the test needs.

    ``CurveOpen2d`` cannot produce a broken or a branching chain -- it builds one from an ordered
    point list -- so the refusals for those are reached with a path built by hand. Only what the
    sampler reads is here: the path's own points, its legs, and the member's placement.
    """
    return types.SimpleNamespace(
        placement=Placement(),
        curve=types.SimpleNamespace(points3d=points3d, segments3d=[_StubSegment(a, b) for a, b in spans]),
    )


def leg_lengths(legs):
    """Each leg's length: its sampled arc length if it is curved, its chord if it is not."""
    out = []
    for leg in legs:
        if leg.is_curved:
            out.append(leg.curve_length)
        else:
            out.append(float(np.linalg.norm(np.asarray(leg.p2) - np.asarray(leg.p1))))
    return out


def test_a_swept_path_is_one_leg_per_segment_with_the_closer_dropped():
    """The lift: a chain is drawn leg by leg rather than refused, and the closer is not a leg.

    ``CurveOpen2d`` decomposes the three-point filleted corner ``[(0,0), (2,0,r=0.5), (2,2)]``
    into **four** ``segments3d`` -- a line from the *last* point back to the first, then line,
    arc, line -- so the first thing this has to get right is which of the four is the member's
    axis. The closer is identified as the one leg spanning the path's own two ends and dropped;
    what is left is walked into a chain from the first point. Drawing the chain as one spline
    would round the corner off and drawing it as one chord would lose it, which is why each leg
    is its own wire.
    """
    bm = a_swept_beam([(0.0, 0.0), (2.0, 0.0, 0.5), (2.0, 2.0)], name="filleted")
    p1, p2 = beam_endpoints(bm)

    legs = sample_member_legs(bm, p1, p2, 1e-04)

    assert [leg.is_curved for leg in legs] == [False, True, False], "line, arc, line -- and no closer"
    assert legs[0].p1 == pytest.approx(p1), "the chain starts on the member's own first node"
    assert legs[-1].p2 == pytest.approx(p2), "and ends on its last"
    for ahead, behind in zip(legs[:-1], legs[1:]):
        assert ahead.p2 == pytest.approx(behind.p1), "each leg has to start where the one before it ended"
    # 1.5 + a quarter circle of radius 0.5 + 1.5, the fillet's arc and not its chord.
    assert leg_lengths(legs) == pytest.approx([1.5, math.pi * 0.5 / 2.0, 1.5], rel=1e-09)


def test_a_two_point_sweep_is_the_one_straight_leg_it_describes():
    """Two points are one leg and no closer, so the straight case needs no special handling."""
    bm = a_swept_beam([(0.0, 0.0), (2.0, 0.0)], name="flat")

    legs = sample_member_legs(bm, *beam_endpoints(bm), 1e-04)

    assert len(legs) == 1 and not legs[0].is_curved
    assert legs[0].p1 == pytest.approx((0.0, 0.0, 0.0)) and legs[0].p2 == pytest.approx((2.0, 0.0, 0.0))


def test_a_swept_path_that_runs_the_other_way_is_turned_rather_than_refused():
    """Which way round a path was authored says nothing about the member, as for a single curve."""
    forwards = a_swept_beam([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)], name="f")
    p1, p2 = beam_endpoints(forwards)

    ahead = sample_member_legs(forwards, p1, p2, 1e-04)
    behind = sample_member_legs(forwards, p2, p1, 1e-04)

    assert [leg.p1 for leg in behind] == [leg.p2 for leg in reversed(ahead)]
    assert [leg.p2 for leg in behind] == [leg.p1 for leg in reversed(ahead)]


def test_a_swept_path_whose_legs_do_not_chain_is_refused_with_the_point_they_part_at():
    """A defensive refusal, and the one shape of path that would otherwise be drawn wrong.

    ``CurveOpen2d`` always produces a chain, so this is reached through the sampler's own
    front door with a stand-in path rather than through a container that cannot make one. Both
    halves matter: a gap means nothing says which way the member runs, and a *branch* -- two
    legs leaving the same point -- is not one member's axis at all. Drawing either would
    produce a member whose wires are somewhere the model never put them.
    """
    from ada.cadit.cae.curves import _sweep_legs

    # Each stub carries its closer, so that what these reach is the chain walk and not the
    # closer check the test below is about.
    gapped = _stub_sweep(
        [(0, 0, 0), (3, 0, 0)],
        [((3, 0, 0), (0, 0, 0)), ((0, 0, 0), (1, 0, 0)), ((2, 0, 0), (3, 0, 0))],
    )
    legs, why = _sweep_legs(gapped, (0, 0, 0), (3, 0, 0), 1e-04)
    assert legs is None
    assert "does not chain" in why and "(1.0, 0.0, 0.0)" in why

    branched = _stub_sweep(
        [(0, 0, 0), (2, 0, 0)],
        [((2, 0, 0), (0, 0, 0)), ((0, 0, 0), (1, 0, 0)), ((1, 0, 0), (2, 0, 0)), ((1, 0, 0), (1, 1, 0))],
    )
    legs, why = _sweep_legs(branched, (0, 0, 0), (2, 0, 0), 1e-04)
    assert legs is None
    assert "does not chain" in why and "2 of its remaining" in why


def test_a_swept_path_with_no_single_closer_is_refused_rather_than_guessed_at():
    """Dropping the closer only works while exactly one leg *is* the closer.

    Two legs between the path's two ends, or none, and nothing says which of them the member
    runs along -- so the writer says so instead of picking one and building a different member.
    """
    from ada.cadit.cae.curves import _sweep_legs

    doubled = _stub_sweep(
        [(0, 0, 0), (2, 0, 0)],
        [((0, 0, 0), (2, 0, 0)), ((2, 0, 0), (0, 0, 0)), ((0, 0, 0), (1, 1, 0)), ((1, 1, 0), (2, 0, 0))],
    )

    legs, why = _sweep_legs(doubled, (0, 0, 0), (2, 0, 0), 1e-04)

    assert legs is None
    assert "2 of them run between the path's own two ends" in why


def test_n1_is_checked_along_every_leg_of_a_swept_member():
    """One ``n1`` for the whole member means every leg has to stay clear of it, not just the first.

    ``N1_COSINES`` gives Abaqus one vector and it projects that vector perpendicular to each
    element's tangent, so a leg running *along* ``n1`` leaves nothing to project and the profile
    is turned whichever way the kernel chooses. This path detours perpendicular to its own
    chord, which puts a whole leg exactly along the ``n1`` the chord gives it -- the failure a
    single-leg member cannot have, reached through a member whose other two legs are fine.
    """
    bm = a_swept_beam([(0.0, 0.0), (0.0, 2.0), (4.0, 2.0), (4.0, 0.0)], name="detour")
    part = ada.Part("Detour")
    part.add_beam(bm)
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    assert beam_n1(bm) == pytest.approx((0.0, 1.0, 0.0)), "the fixture only bites while n1 is the y-axis"

    with pytest.raises(UnsupportedBeamError) as raised:
        build_plan(assembly)

    message = str(raised.value)
    assert "comes within sin=0" in message
    assert "turned an arbitrary way round" in message


def test_a_swept_member_is_one_sub_edge_per_leg_and_one_vertex_per_junction():
    """The topology guard's numbers for a chain, and they are measured, not derived.

    Abaqus 2025, three wires drawn line-arc-line through shared end points with
    ``mergeType=IMPRINT``: ``edges=3 vertices=4``. The junction vertex merges exactly as a real
    joint's does -- unmerged would be 6 vertices -- and the mesh puts one node on it. So a
    swept member is one member with a sub-edge count of one per leg, and the part's vertex
    count carries its junctions.
    """
    filleted = build_plan(_swept_model([(0.0, 0.0), (2.0, 0.0, 0.5), (2.0, 2.0)], "filleted"))
    ell = build_plan(_swept_model([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)], "ell"))

    assert filleted.parts[0].topology.edges_per_member == {"filleted": 3}
    assert filleted.parts[0].topology.vertices == 4
    assert ell.parts[0].topology.edges_per_member == {"ell": 2}
    assert ell.parts[0].topology.vertices == 3


def test_the_stated_bounding_box_covers_a_swept_members_legs_and_not_only_its_ends(tmp_path):
    """A swept path can reach well outside the box its own two end nodes span.

    The in-kernel guard compares the part's **vertices** against this box, and a junction is a
    vertex -- so a box taken from the member's ends alone would fail a perfectly good build for
    a member like this one, whose two ends both sit on ``y = 0`` while its middle two legs run
    along ``y = 2``. The box has to hold every vertex and no more: a curve's *sample* points are
    interior to an edge and are deliberately not in it.
    """
    # The same detour shape as the n1 test, turned so that its n1 is out of the path's plane and
    # therefore clear of every leg -- the refusal above is about n1, and this test is not.
    bm = a_swept_beam([(0.0, 0.0), (0.0, 2.0), (4.0, 2.0), (4.0, 0.0)], name="detour", up=(0.0, 1.0, 0.0))
    part = ada.Part("Detour")
    part.add_beam(bm)
    assembly = ada.Assembly("A")
    assembly.add_part(part)
    assert beam_endpoints(bm) == ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0)), "both ends on y=0, or this proves nothing"

    _, text = emit(assembly, tmp_path)

    namespace = {}
    start = text.index("EXPECTED_BBOX = {")
    exec(text[start : text.index("\n}\n", start) + 3], namespace)  # noqa: S102 - literals from the script
    low, high = namespace["EXPECTED_BBOX"]["Detour"]
    assert low == pytest.approx((0.0, 0.0, 0.0))
    assert high == pytest.approx((4.0, 2.0, 0.0)), "the junctions at y=2 are vertices and have to be in the box"


def test_a_brace_landing_on_a_straight_leg_of_a_sweep_splits_that_leg_and_nothing_else():
    """The per-leg prediction is a real prediction: an imprint on one leg is counted on the member.

    A landing member splits the leg it lands on, and the member's sub-edge count is the total
    over its legs -- so the L's two legs plus the split make 3, and the brace's own 1 makes the
    part 4 edges. Counting the legs as one member and then predicting one sub-edge for it would
    read 2 here and fail in the kernel.
    """
    ell = a_swept_beam([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)], name="ell")
    brace = ada.Beam("brace", (1.0, 0.0, 0.0), (1.0, -2.0, 0.0), "IPE300", "S355")
    part = ada.Part("Landing")
    part.add_beam(ell)
    part.add_beam(brace)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    topology = build_plan(assembly).parts[0].topology

    assert topology.edges_per_member == {"brace": 1, "ell": 3}
    assert topology.edges == 4
    assert topology.splits["ell"] == ((1.0, 0.0, 0.0),)
    assert topology.vertices == 5, "the L's corner and two ends, the brace's far end, and the landing point"


def test_a_curved_member_is_drawn_as_a_spline_and_located_by_its_own_points(tmp_path):
    _, text = emit(a_curved_model("BeamCurved"), tmp_path)

    assert "WireSpline(points=curve_0_0, mergeType=IMPRINT, meshable=ON, smoothClosedSpline=OFF)" in text
    assert "WirePolyLine" not in text, "the only member here is curved"
    assert "_curved_member_edges('Curves', 'BeamCurved', part_0, curve_0_0, " in text
    assert ".edges.getByBoundingCylinder(" not in text
    # ... and the arc length the guard compares against is stated in the script itself.
    assert (
        "_curved_member_edges('Curves', 'BeamCurved', part_0, curve_0_0, {0!r})".format(QUARTER_ARC_LENGTH)[:60] in text
    )


def test_a_mixed_part_keeps_the_cylinder_for_its_straight_members(tmp_path):
    """The two location strategies coexist, and neither is applied to the other's member.

    A cylinder round a curve finds nothing -- measured, 0 edges round a quarter arc's chord --
    and ``findAt`` on a straight member that a brace might split would section part of it and
    leave the rest bare. So the split is not a tidiness question.
    """
    straight = ada.Beam("tie", (0.0, 2.0, 0.0), (0.0, 5.0, 0.0), "IPE300", "S355")

    _, text = emit(a_curved_model("BeamRevolve", extra=[straight]), tmp_path)

    assert text.count("WireSpline(") == 1
    assert text.count("WirePolyLine(") == 1
    assert text.count("= _curved_member_edges(") == 1
    assert text.count(".edges.getByBoundingCylinder(") == 1
    assert text.count("_member_edges('Curves', 'tie'") == 1


def test_a_curved_member_is_one_sub_edge_and_adds_only_its_two_endpoints():
    """Built for real in Abaqus 2025: an arc plus a straight member sharing its end gives
    ``edges=2 vertices=3``, and the arc alone gives ``edges=1 vertices=2`` at every sample
    count from 3 to 65 points.
    """
    straight = ada.Beam("tie", (0.0, 2.0, 0.0), (0.0, 5.0, 0.0), "IPE300", "S355")
    topology = build_plan(a_curved_model("BeamRevolve", extra=[straight])).parts[0].topology

    assert topology.edges_per_member == {"BeamRevolve": 1, "tie": 1}
    assert topology.edges == 2
    assert topology.vertices == 3
    assert topology.splits["BeamRevolve"] == ()


def test_a_member_landing_on_a_curves_interior_is_refused_not_predicted():
    """Measured: a straight wire landing on a spline wire's midpoint imprints it.

    The part went from ``2 edges / 3 vertices`` to ``3 edges / 4 vertices``, so the split is
    real. Where along the spline CAE puts the new vertex is the spline's own
    parameterisation's business, though, so the member's sub-edge count cannot be stated from
    anything adapy holds -- and this writer asserts every member's sub-edge count. Refusing is
    what keeps that assertion true of every model it accepts.
    """
    arc = a_curved_beam("BeamCurved", name="arc")
    points, _ = sample_member_curve(arc, *beam_endpoints(arc), 1e-04)
    middle = points[len(points) // 2]
    brace = ada.Beam("brace", middle, (middle[0] + 1.0, middle[1] + 1.0, 0.0), "IPE300", "S355")
    part = ada.Part("X")
    part.add_beam(arc)
    part.add_beam(brace)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    with pytest.raises(CaeWriteError) as raised:
        build_plan(assembly)

    message = str(raised.value)
    assert "meets the curved member 'arc'" in message
    assert "away from that curve's ends" in message
    assert "2 edges / 3 vertices to 3 edges / 4 vertices" in message


def test_a_curves_endpoint_still_splits_the_straight_member_it_lands_inside():
    """Adding a curve to a part must not weaken the prediction for anything else in it.

    The curve's own ends are points like any other, so the existing arithmetic applies to
    them -- measured in the kernel: a straight member whose interior an arc's endpoint lands
    on came back as 2 sub-edges, and its bounding cylinder found both.
    """
    arc = a_curved_beam("BeamCurved", name="arc")  # runs (2,0,0) -> (0,2,0)
    through = ada.Beam("through", (0.0, 0.0, 0.0), (4.0, 0.0, 0.0), "IPE300", "S355")
    part = ada.Part("Y")
    part.add_beam(arc)
    part.add_beam(through)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    topology = build_plan(assembly).parts[0].topology

    assert topology.edges_per_member == {"arc": 1, "through": 2}
    assert topology.splits["through"] == ((2.0, 0.0, 0.0),)
    assert topology.vertices == 4


def test_a_member_clear_of_a_curve_is_not_refused():
    """Refusing every part that happens to contain a curve would be the easy wrong answer."""
    arc = a_curved_beam("BeamCurved", name="arc")
    clear = ada.Beam("clear", (0.0, 0.0, 3.0), (4.0, 0.0, 3.0), "IPE300", "S355")
    part = ada.Part("Z")
    part.add_beam(arc)
    part.add_beam(clear)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    topology = build_plan(assembly).parts[0].topology

    assert topology.edges_per_member == {"arc": 1, "clear": 1}


def test_the_crossing_scan_reads_the_curves_whole_path_and_not_its_chord():
    """The pair scan filters by bounding box, and an arc reaches outside the box of its ends.

    A 144-degree arc of radius 2 starting on the +x axis has its endpoints at y = 0 and
    y = 1.176, so the box of its two *ends* stops at y = 1.176 -- while the arc itself reaches
    y = 2. A brace standing on the arc's topmost point therefore shares no bounding box with
    the chord at all, and a scan that took the box from the endpoints would never compare the
    pair: the refusal would go quiet on a crossing CAE will happily imprint.

    The first version of this test used a quarter arc and did **not** catch that, because a
    quarter arc's endpoint box already contains the whole arc. The two assertions below are
    what make this one about the box: the arc passes through the point, and the point is
    outside the box of the ends. ``find_crossings`` on the chord alone is checked as well, so
    the difference is demonstrated rather than asserted of the implementation.
    """
    curve = exact_arc_spline(radius=2.0, start=0.0, end=0.8 * math.pi)
    p1 = tuple(float(c) for c in curve.control_points_list[0])
    p2 = tuple(float(c) for c in curve.control_points_list[-1])
    arc = BeamCurved("arc", p1, p2, curve, "IPE300", mat="S355")
    points, _ = sample_member_curve(arc, *beam_endpoints(arc), 1e-04)
    top = (0.0, 2.0, 0.0)
    assert (
        min(np.linalg.norm(np.asarray(points) - np.asarray(top), axis=1)) < 1e-02
    ), "the fixture's arc must pass through its topmost point, or this test is not the one described"
    assert max(p1[1], p2[1]) < top[1] - 0.5, "and that point must lie outside the box of the arc's own ends"

    brace = ada.Beam("brace", (0.0, 2.0, -1.0), (0.0, 2.0, 1.0), "IPE300", "S355")
    chord_only = [
        Segment(name="arc", p1=p1, p2=p2),
        Segment(name="brace", p1=(0.0, 2.0, -1.0), p2=(0.0, 2.0, 1.0)),
    ]
    assert find_crossings(chord_only, 1e-04) == [], "the chord does not come near this brace"

    part = ada.Part("W")
    part.add_beam(arc)
    part.add_beam(brace)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    with pytest.raises(CaeWriteError, match="meets the curved member 'arc'"):
        build_plan(assembly)


def test_a_curved_member_does_not_turn_the_per_member_prediction_off_for_the_part():
    """The point of refusing contact on a curve: nothing in the part goes unasserted.

    Every member, curved or not, has a stated sub-edge count, and the part has a stated edge
    and vertex total -- which is what the emitted script compares the kernel against.
    """
    straight = ada.Beam("tie", (0.0, 2.0, 0.0), (0.0, 5.0, 0.0), "IPE300", "S355")
    brace = ada.Beam("brace", (0.0, 3.5, 0.0), (2.0, 3.5, 0.0), "IPE300", "S355")
    plan = build_plan(a_curved_model("BeamRevolve", extra=[straight, brace]))
    topology = plan.parts[0].topology

    assert sorted(topology.edges_per_member) == ["BeamRevolve", "brace", "tie"]
    assert topology.edges_per_member == {"BeamRevolve": 1, "brace": 1, "tie": 2}
    assert topology.edges == 4
    assert topology.vertices == 5


def test_the_expected_topology_of_a_curve_is_stated_in_the_emitted_script(tmp_path, monkeypatch):
    straight = ada.Beam("tie", (0.0, 2.0, 0.0), (0.0, 5.0, 0.0), "IPE300", "S355")
    _, text = emit(a_curved_model("BeamRevolve", extra=[straight]), tmp_path)

    namespace, _ = load_emitted_script(text, tmp_path, monkeypatch)

    assert namespace["EXPECTED_TOPOLOGY"] == {
        "Curves": {
            "edges": 2,
            "wire_edges": 2,
            "vertices": 3,
            "faces": 0,
            "stringers": [],
            "edges_per_member": {"BeamRevolve": 1, "tie": 1},
        }
    }
    assert namespace["CURVE_LENGTH_REL_TOL"] == 1e-04


# --------------------------------------------------------------------------------------
# The curved member's own in-kernel guard, against a stand-in edge array
# --------------------------------------------------------------------------------------


class FakeCurveEdge:
    """``getFaces`` returns nothing on purpose: a curved member is always a wire here, because a
    curved member lying on a plate is refused while planning -- so its edge must bound no face, and
    the locator now asks. An ordinary edge that bounds a face produces no beam elements at all."""

    def __init__(self, index, length):
        self.index = index
        self._length = length
        self.pointOn = ((float(index), 0.0, 0.0),)

    def getSize(self, printResults=True):
        return self._length

    def getFaces(self):
        return ()


class FakeCurveEdges(list):
    """A part's edge array, for the curved-member locator and nothing else.

    ``hits`` is what each successive ``findAt`` returns, as a list of edge indices -- measured
    behaviour on Abaqus 2025 is that ``findAt`` warns and returns an **empty** sequence for a
    point that is on no edge, rather than raising, so an empty list here is a faithful
    stand-in for that and not an invention.
    """

    def __init__(self, hits, lengths):
        super().__init__([FakeCurveEdge(index, lengths[index]) for index in range(len(lengths))])
        self._hits = [list(hit) for hit in hits]

    def findAt(self, *specs):
        indices = self._hits.pop(0) if self._hits else []
        return [self[index] for index in indices]


class FakeCurvedPart:
    def __init__(self, edges):
        self.edges = edges


ARC_POINTS = ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 1.5, 0.0), (3.0, 1.0, 0.0), (4.0, 0.0, 0.0))


def curved_guard(tmp_path, monkeypatch, hits, lengths, expected):
    """Run the emitted script's own curved-member locator over a stand-in part."""
    _, text = emit(a_curved_model("BeamCurved"), tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    part = FakeCurvedPart(FakeCurveEdges(hits, lengths))
    result = namespace["_curved_member_edges"]("Curves", "arc", part, ARC_POINTS, expected)
    return namespace, exits, result


def read_error(tmp_path):
    return json.loads((tmp_path / "out.cae_build_result.json").read_text())["errors"][0]


def test_the_curved_guard_passes_when_every_sample_is_on_one_edge_of_the_right_length(tmp_path, monkeypatch):
    namespace, exits, edges = curved_guard(tmp_path, monkeypatch, [[0], [0], [0]], [5.0], 5.0)

    assert exits == []
    assert [edge.index for edge in edges] == [0]
    assert namespace["_RESULT"]["edges_per_member"]["arc"] == 1
    assert namespace["_RESULT"]["curve_length_error"]["arc"] == 0.0


def test_the_curved_guard_catches_a_sample_point_that_is_on_no_edge(tmp_path, monkeypatch):
    """Measured: ``findAt`` 1.4e-03 off a spline already reports "could not find a geometric
    entity" and returns an empty sequence. So this is the check that the wire CAE built
    actually passes through the points adapy sampled.
    """
    curved_guard(tmp_path, monkeypatch, [[0], [], [0]], [5.0], 5.0)

    assert "lies on no edge at all" in read_error(tmp_path)


def test_the_curved_guard_catches_a_curve_cae_split_anyway(tmp_path, monkeypatch):
    """The backstop for a contact on a curve that the plan-time refusal did not see."""
    curved_guard(tmp_path, monkeypatch, [[0], [0], [1]], [3.0, 2.0], 5.0)

    error = read_error(tmp_path)
    assert "lie on 2 different edges" in error
    assert "sub-edge count is now unknown" in error


def test_the_curved_guard_catches_a_wire_that_is_not_that_curve(tmp_path, monkeypatch):
    """A chord instead of the arc is about 10% short, which is four orders past the tolerance."""
    curved_guard(tmp_path, monkeypatch, [[0], [0], [0]], [4.5], 5.0)

    error = read_error(tmp_path)
    assert "adapy sampled its arc length as 5.0 and CAE built 4.5" in error
    assert "relative difference of 0.1" in error


def test_the_curved_guard_tolerates_the_splines_own_interpolation_error(tmp_path, monkeypatch):
    """2.6e-06 is what the emitted sampling measured, so 1e-04 must not be tight enough to fail it."""
    _, exits, _ = curved_guard(tmp_path, monkeypatch, [[0], [0], [0]], [5.0 * (1.0 - 2.6e-06)], 5.0)

    assert exits == []


# --------------------------------------------------------------------------------------
# The offset's own in-kernel guard
# --------------------------------------------------------------------------------------


class FakeSection:
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


def offset_guard(tmp_path, monkeypatch, sections):
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE300", e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0)))
    _, text = emit(assembly, tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    namespace["_guard_section_offsets"](FakeModel({}, sections=sections))
    return namespace, exits


def test_the_offset_guard_passes_when_cae_holds_what_adapy_projected(tmp_path, monkeypatch):
    namespace, exits = offset_guard(
        tmp_path,
        monkeypatch,
        {"sec_IPE300_S355_off_0_0p4": FakeSection(beamSectionOffset=[0.0, 0.4])},
    )

    assert exits == []
    assert namespace["_RESULT"]["section_offsets"] == {"sec_IPE300_S355_off_0_0p4": [[0.0, 0.4], "beamSectionOffset"]}


def test_the_offset_guard_catches_an_offset_that_did_not_arrive(tmp_path, monkeypatch):
    """CAE ignores an offset in every geometric query it offers, so nothing else would notice."""
    _, exits = offset_guard(
        tmp_path,
        monkeypatch,
        {"sec_IPE300_S355_off_0_0p4": FakeSection(beamSectionOffset=[0.0, 0.0])},
    )

    assert exits == [1]
    error = read_error(tmp_path)
    assert "adapy projected the offset (0.0, 0.4) onto its (n1, n2) axes and CAE holds (0.0, 0.0)" in error
    assert "eccentricity silently dropped" in error


def test_the_offset_guard_catches_a_section_that_was_never_created(tmp_path, monkeypatch):
    _, exits = offset_guard(tmp_path, monkeypatch, {})

    assert exits == [1]
    assert "carries an offset but does not exist" in read_error(tmp_path)


def test_the_offset_guard_reads_the_keyword_the_section_kind_actually_takes(tmp_path, monkeypatch):
    """A generalized section's offset lives in ``centroid``; reading ``beamSectionOffset``
    there would read an attribute CAE refuses to let the script write, and would therefore
    always find (0, 0) and always fail.
    """
    assembly = ada.Assembly("A")
    part = assembly.add_part(ada.Part("P"))
    part.add_beam(ada.Beam("bm", (0, 0, 0), (0, 0, 3), general_section(), e1=(0.0, 0.4, 0.0), e2=(0.0, 0.4, 0.0)))
    _, text = emit(assembly, tmp_path)
    namespace, exits = load_emitted_script(text, tmp_path, monkeypatch)
    section = FakeSection(centroid=[0.0, 0.4], beamSectionOffset=[0.0, 0.0])

    namespace["_guard_section_offsets"](FakeModel({}, sections={"sec_GEN_S355_off_0_0p4": section}))

    assert exits == []
    assert namespace["_RESULT"]["section_offsets"]["sec_GEN_S355_off_0_0p4"][1] == "centroid"


def test_a_curve_whose_tangent_turns_onto_its_own_n1_is_refused():
    """The orientation failure a straight member cannot have.

    ``N1_COSINES`` gives Abaqus one vector for the whole member and it projects that vector
    perpendicular to each element's tangent, so where the two line up there is nothing left to
    project. A straight member has one tangent and :func:`beam_n1` already refuses a degenerate
    ``yvec``; a curve's tangent turns, and an arc that turns nearly 180 degrees sweeps its
    tangent right onto an ``n1`` lying in the arc's own plane. Measured on this fixture, the
    smallest sin between the two falls from 0.71 at a quarter arc to 0.039 at 0.98 pi.
    """
    curve = exact_arc_spline(radius=2.0, start=0.0, end=0.98 * math.pi)
    p1 = tuple(float(c) for c in curve.control_points_list[0])
    p2 = tuple(float(c) for c in curve.control_points_list[-1])
    # up along the arc's own axis puts n1 in the arc's plane, where the tangent also lives.
    bm = BeamCurved("swinger", p1, p2, curve, "IPE300", mat="S355", up=(0.0, 0.0, 1.0))
    part = ada.Part("Swing")
    part.add_beam(bm)
    assembly = ada.Assembly("A")
    assembly.add_part(part)

    with pytest.raises(UnsupportedBeamError) as raised:
        build_plan(assembly)

    message = str(raised.value)
    assert "comes within sin=" in message
    assert "turned an arbitrary way round" in message


def test_an_arc_whose_n1_stays_clear_of_its_tangent_is_not_refused():
    """The ordinary case: a stiffener arc in a plane, oriented out of that plane.

    Refusing every curve would be the easy wrong answer here too, so the fixture that passes
    is asserted next to the one that does not.
    """
    bm = a_curved_beam("BeamCurved", name="arc")
    points, _ = sample_member_curve(bm, *beam_endpoints(bm), 1e-04)
    n1 = np.asarray(beam_n1(bm))
    tangents = np.diff(np.asarray(points), axis=0)
    tangents = tangents / np.linalg.norm(tangents, axis=1)[:, None]

    check_n1_holds_along_the_curve(bm, points)

    assert float(np.linalg.norm(np.cross(tangents, n1), axis=1).min()) > CURVE_N1_MIN_SIN


def test_the_curve_and_offset_machinery_stays_inside_the_python_2_syntax_floor(tmp_path):
    """The existing floor test uses a straight, offset-free frame, so it never sees this code.

    Two whole helper functions and a data table are emitted only when a model needs them, and
    a f-string in either would be a SyntaxError in an older Abaqus kernel rather than an error
    anyone could read.
    """
    straight = ada.Beam("tie", (0.0, 2.0, 0.0), (0.0, 5.0, 0.0), "IPE300", "S355")
    offset = ada.Beam("off", (0.0, 8.0, 0.0), (4.0, 8.0, 0.0), "IPE300", "S355", e1=(0, 0, -0.4), e2=(0, 0, -0.4))
    _, text = emit(a_curved_model("BeamRevolve", extra=[straight, offset]), tmp_path)

    assert "_curved_member_edges" in text and "_guard_section_offsets" in text, "the fixture must emit both"
    tree = ast.parse(text)
    forbidden = {"JoinedStr", "FormattedValue", "AnnAssign", "NamedExpr"}
    assert sorted({type(node).__name__ for node in ast.walk(tree)} & forbidden) == []


def test_the_segment_distance_solve_agrees_with_brute_force():
    """The one piece of non-obvious arithmetic the curve refusal rests on.

    ``_segment_to_polyline`` is the clamped segment/segment closest-approach solve, written
    branchlessly so it runs over a whole polyline at once -- and a mis-clamped branch there
    would make the refusal miss a crossing rather than raise, which is the failure mode that
    does not announce itself. So it is checked against a dense parameter sweep over random
    pairs, including the parallel, touching and zero-length cases the closed form has to
    special-case.
    """
    from ada.cadit.cae.topology import _segment_to_polyline

    rng = np.random.default_rng(20250926)
    pairs = [
        # random, then the awkward ones: parallel, collinear, crossing, and degenerate
        (rng.normal(size=(2, 3)), rng.normal(size=(2, 3))),
        (np.array([[0.0, 0, 0], [4, 0, 0]]), np.array([[0.0, 1, 0], [4, 1, 0]])),
        (np.array([[0.0, 0, 0], [4, 0, 0]]), np.array([[5.0, 0, 0], [9, 0, 0]])),
        (np.array([[0.0, 0, 0], [4, 0, 0]]), np.array([[2.0, -1, 0], [2, 1, 0]])),
        (np.array([[0.0, 0, 0], [4, 0, 0]]), np.array([[2.0, 0, 0], [2, 0, 0]])),
        (np.array([[1.0, 1, 1], [1, 1, 1]]), np.array([[0.0, 0, 0], [2, 0, 0]])),
    ] + [(rng.normal(size=(2, 3)), rng.normal(size=(2, 3))) for _ in range(40)]
    sweep = np.linspace(0.0, 1.0, 401)

    for first, second in pairs:
        distance, _, _ = _segment_to_polyline(
            first[0], first[1] - first[0], second[:1], (second[1] - second[0])[None, :]
        )
        on_first = first[0] + sweep[:, None] * (first[1] - first[0])
        on_second = second[0] + sweep[:, None] * (second[1] - second[0])
        brute = np.linalg.norm(on_first[:, None, :] - on_second[None, :, :], axis=2).min()

        assert float(distance[0]) <= brute + 1e-09, "the closed form must not be beaten by a coarse sweep"
        assert float(distance[0]) == pytest.approx(brute, abs=5e-03), "nor miss the minimum by a margin"
