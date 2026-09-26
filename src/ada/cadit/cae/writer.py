"""Write an Abaqus/CAE script that rebuilds a concept model as **editable geometry**.

adapy's existing Abaqus output is an INP. Importing one into CAE was measured
(``CAE_PROBED_FACTS`` §8) and it carries materials, typed profiles, beam sections,
section assignments and sets faithfully — but the geometry arrives as an *orphan
mesh*: ``edges 0, faces 0, cells 0``. Nothing to re-mesh, nothing to attach a brace
to, nothing to edit. The user's verdict was "orphan mesh is not enough".

So this writer's subject is geometry and connectivity. It builds **beams**: straight ones
as ``WirePolyLine``, curved ones as ``WireSpline`` through points on their exact curve, and
a constant eccentricity as the section's own ``beamSectionOffset``. Anything a wire would
silently misrepresent is refused rather than approximated, and anything not translated at
all is listed in the emitted script's header and in its result sidecar so its absence is
visible.

Eight guards carry the correctness of the output, each aimed at a specific way this
could emit a model that opens in CAE, meshes, solves and is wrong:

1. **Every edge ends with exactly one section assignment**, asserted inside the
   emitted script against the kernel's own ``sectionAssignments``. Imprinting can
   leave a sub-edge no cylinder claimed, and a duplicated wire is a second edge no
   cylinder claims either. What this guard does **not** see is connectivity, although
   it was written believing it did: measured, a disconnected frame produces exactly the
   same "every edge carries a section" verdict as a connected one. That is guard 6.
2. **A beam's shape is checked by exact type.** A straight ``Beam`` and the three curved
   classes are built; ``BeamTapered`` is refused because writing the INP for one
   **segfaults the Abaqus 2025 kernel** (measured, with the taper isolated as the cause),
   and an unknown subclass is refused for being unknown. A curved member whose axis cannot
   be handed over as one spline wire -- a multi-leg sweep path, a curve container nobody has
   sampled -- is refused too, and named. A ``BeamRevolve`` drawn as a straight chord is a
   model that looks right, which is what all of this is for.
3. **An eccentricity is carried as a section offset, and refused when it cannot be.**
   ``*Beam Section Offset`` takes one 2-tuple in the section's own ``(n1, n2)`` axes, so a
   constant offset is exact and cheap -- no extra nodes and no ``*MPC BEAM`` links, which is
   what adapy's INP writer has to create. A **varying** offset (``e1 != e2``), an **axial**
   component, and an offset on a **curved** member are refused: none of the three is
   expressible as one 2-tuple, and a wire drawn end to end discards an offset silently,
   which is the 660 mm coordinate error this project has already paid for, in a new coat.
4. **Endpoints come from** :meth:`ada.Beam.axis_global`, whose own docstring says
   exporters must share it so they cannot disagree about where a beam is. Raw
   ``n1.p``/``n2.p`` ignores the owning Part's placement.
5. **A result sidecar and a non-zero exit.** CAE can exit 0 on a half-built model, so
   the script writes ``<stem>.cae_build_result.json`` and forces a non-zero status on
   failure. Without it, "it ran" cannot be told from "it built a third of the model".

   Two measurements shaped this, and both contradict the obvious implementation.
   ``sys.exit(1)`` inside ``abaqus cae noGUI=script.py`` does **not** reach the process
   exit status: the run still reports 0, because CAE treats a ``SystemExit`` leaving the
   script as a clean finish. ``os._exit(1)`` does reach it — CAE then prints
   ``Abaqus Error: cae exited with an error``. But the ``abq<ver>.bat`` launcher
   *itself* still returns 0 either way, so a caller cannot learn the outcome from an
   exit code at all. The sidecar is the signal, which is why it is not optional.
6. **The topology CAE built is the topology adapy described.** Per-member sub-edge
   counts and the part's vertex count, computed from the adapy model by
   :mod:`ada.cadit.cae.topology` and asserted in the emitted script against the kernel.
   This is the *only* guard that can tell a connected frame from a pile of loose
   sticks — guard 1 cannot, and two collinear members that failed to join do not even
   change the edge count, only the vertex count. A *crossing*, which CAE welds
   silently, is refused while planning rather than asserted; the reasoning is in
   :func:`ada.cadit.cae.topology.expected_topology` and in :func:`build_plan`.
7. **No planned name already exists in the target CAE model.** CAE does not raise on a
   reused name: it silently *replaces* the object and invalidates handles to the old
   one (measured), so a second run in the same GUI session would quietly swap every
   part. Checked against the live model before a single object is built.
8. **A curve is the curve adapy sampled, and an offset is the offset adapy projected.** A
   spline through points on a curve is an interpolation, so the built edge's arc length is
   compared against the sampled one and every interior sample point has to lie on that one
   edge. An offset is read back off the section CAE holds -- which is a readback and not a
   weighing on purpose: probed, ``getMassProperties()`` reports the same mass and the same
   centre of mass with an offset and without, so CAE's own mass model cannot see one at all.
   The solver can, and that is where the sign of the projection was pinned; see
   :func:`beam_section_offset`.

``unit_scale`` is accepted only as ``1.0``. It used to multiply coordinates and profile
dimensions, which is a trap and not a feature: ``E`` and the density were left alone, so
``unit_scale=1000`` emitted ``IProfile(h=300.0)`` beside ``Elastic(table=((2.1e11, 0.3),))``
— a millimetre model a million times too stiff, with nothing anywhere reporting it. No
single scalar can fix that, because ``E`` scales as s⁻² and density as s⁻³ *relative to a
mass unit that is a separate choice* (N/mm/s needs tonnes, not kilogrammes). So the
conversion belongs to the model, not to the writer, and a non-unit scale is refused.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ada.config import Config, get_logger

from .curves import (
    CURVE_LENGTH_REL_TOL,
    MAX_TURN_RADIANS,
    CurveNotSupported,
    is_curved_beam_type,
    sample_member_curve,
)
from .names import CaeNameError, NameRegistry, dump_name_map
from .topology import (
    CAE_MERGE_TOL,
    PartTopology,
    Segment,
    expected_topology,
    find_crossings,
)

if TYPE_CHECKING:
    from ada import Beam, Material, Part, Section
    from ada.sections.profiles import ProfileSpec

logger = get_logger()

#: The ``Beam`` subclasses this writer builds. All three carry the **exact** curve — an
#: ngeom ``BSplineCurveWithKnots`` straight off the ACIS body for a ``BeamCurved``, a
#: centre/axis/radius arc for a ``BeamRevolve`` — so nothing is approximated on the way in;
#: see :mod:`ada.cadit.cae.curves`.
CURVED_BEAM_TYPES = ("BeamCurved", "BeamRevolve", "BeamSweep")

#: ``Beam`` subclasses that are refused, and why. The refusal is an exact-type test, so a
#: fifth subclass added later is refused too rather than silently chorded.
#:
#: ``BeamTapered`` is not refused out of caution. CAE accepts
#: ``BeamSection(beamShape=TAPERED, profileEnd=...)`` and reads it back, and then **writing
#: the INP segfaults the kernel** — measured on Abaqus 2025, with the curve isolated as
#: innocent and the taper as the cause::
#:
#:     >>> straight_constant  -> WROTE OK
#:     >>> straight_TAPERED   -> *** ABAQUS/ABQcaeK rank 0 encountered a SEGMENTATION FAULT
#:                               Abaqus Error: cae exited with an error code 11 (0XB)
REFUSED_BEAM_TYPES = {
    "BeamTapered": (
        "CAE accepts BeamSection(beamShape=TAPERED, profileEnd=...) and reads it back, but writing "
        "the INP then SEGFAULTS the kernel -- measured on Abaqus 2025, with the two halves isolated "
        "so the taper is provably the cause and the curve provably innocent: "
        "'>>> straight_constant  -> WROTE OK' against "
        "'>>> straight_TAPERED   -> *** ABAQUS/ABQcaeK rank 0 encountered a SEGMENTATION FAULT / "
        "Abaqus Error: cae exited with an error code 11 (0XB)'. A model that cannot be written to a "
        "deck is not a model, so the taper is refused until Abaqus can write one"
    )
}

#: An offset of exactly ``(0, 0, 0)`` is "no offset", not an offset — adapy stores a
#: ``Direction`` either way. Anything above this is a real eccentricity.
ECCENTRICITY_TOL = 1e-09

#: A wire needs two distinct points. Compared against the *emitted* (scaled) length.
MIN_BEAM_LENGTH = 1e-09

#: How the per-member bounding cylinder is sized, as a fraction of the member's own
#: length, so the numbers hold in metres and in millimetres alike.
#:
#: ``radius`` only has to admit the member's own edges, whose endpoints this script
#: placed exactly — it must stay far below the spacing of parallel neighbours (twin
#: girders 100 mm apart are ordinary). ``overshoot`` pushes the end caps just past the
#: end vertices so an edge that reaches a cap is unambiguously inside;
#: ``getByBoundingCylinder`` returns only fully contained edges (measured: a brace
#: touching the cap at a single point was *not* returned), so the overshoot must also
#: stay short enough not to swallow a short collinear neighbour.
#:
#: Both are pure fractions of the member's length, with no absolute floor or ceiling.
#: An absolute clamp would have been the obvious thing and it would have been wrong in
#: exactly the way this writer is meant to guard against: 1 mm is a sane radius for a
#: model in metres and a hundredth of a micron for the same model in millimetres, so a
#: clamp turns a unit change into a silent change of behaviour. A member short enough
#: for these to underflow has already been refused by ``MIN_BEAM_LENGTH``.
CYLINDER_RADIUS_FRACTION = 1e-04
CYLINDER_OVERSHOOT_FRACTION = 1e-04


class CaeWriteError(Exception):
    """This model cannot be expressed as a phase-1 CAE concept model."""


class UnsupportedBeamError(CaeWriteError):
    """A beam that a straight wire would misrepresent rather than merely simplify."""


@dataclass(frozen=True)
class SkippedObject:
    """One physical object this writer does not translate, and why."""

    name: str
    kind: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind, "reason": self.reason}


@dataclass(frozen=True)
class _MaterialRow:
    """One CAE material.

    ``shear`` is carried because a generalised section does not read its properties from
    the material at all — it needs a ``table=((E, G),)`` of its own. See
    :data:`_NON_LINEAR_PROFILE_CLASS`.
    """

    cae_name: str
    young: float
    poisson: float
    density: float
    shear: float

    def describe(self) -> str:
        return "E={0!r} v={1!r} rho={2!r}".format(self.young, self.poisson, self.density)


@dataclass
class _SectionUse:
    """One (profile, material, offset) triple, which is one CAE ``BeamSection``.

    The offset belongs to the section rather than to the member, which is the whole point of
    ``*Beam Section Offset`` -- so two members with the same profile and grade but different
    offsets are two sections.
    """

    cae_section_name: str
    cae_profile_name: str
    cae_material_name: str
    spec: ProfileSpec
    section_name: str
    #: ``(along n1, along n2)`` in the member's own section frame, or ``None`` for no offset.
    offset: tuple[float, float] | None = None


@dataclass
class _MemberPlan:
    """Everything the emitted script needs in order to place and dress one beam.

    A **straight** member carries a cylinder (``cyl1``/``cyl2``/``radius``) and no ``path``;
    a **curved** one carries the sampled ``path`` its spline is drawn through and no
    cylinder. The two are exclusive because they are located differently, and they are
    located differently for a measured reason: a bounding cylinder round a quarter arc's
    chord finds **0 edges**, because ``getByBoundingCylinder`` returns only fully contained
    edges and the arc bulges 0.59 units outside its own chord.
    """

    beam_name: str
    cae_set_name: str
    cae_section_name: str
    p1: tuple[float, float, float]
    p2: tuple[float, float, float]
    n1: tuple[float, float, float]
    cyl1: tuple[float, float, float] | None = None
    cyl2: tuple[float, float, float] | None = None
    radius: float | None = None
    #: The polyline CAE's ``WireSpline`` interpolates. ``None`` for a straight member.
    path: tuple[tuple[float, float, float], ...] | None = None
    #: The sampled arc length of that polyline, which the emitted script checks the built
    #: edge against. ``None`` for a straight member.
    curve_length: float | None = None

    @property
    def is_curved(self) -> bool:
        return self.path is not None


@dataclass
class _PartPlan:
    part_name: str
    cae_part_name: str
    cae_instance_name: str
    members: list[_MemberPlan] = field(default_factory=list)
    #: What CAE must end up holding for this part. ``None`` only while the part is being
    #: filled in; every part in a finished plan carries one.
    topology: PartTopology | None = None


@dataclass
class _Plan:
    """The whole emission, resolved on the adapy side before a line is written."""

    root_name: str
    model_name: str
    units: str
    joint_tol: float = 0.0
    parts: list[_PartPlan] = field(default_factory=list)
    materials: list[_MaterialRow] = field(default_factory=list)
    sections: list[_SectionUse] = field(default_factory=list)
    skipped: list[SkippedObject] = field(default_factory=list)
    registries: dict[str, NameRegistry] = field(default_factory=dict)


def _load_profile_spec():
    """WS-A's section→profile seam, imported late so its absence is a clear message.

    There is deliberately no fallback mapping here: a second table of "which CAE
    profile does this adapy section become" is exactly how the INP path and the CAE
    path would come to disagree.
    """
    try:
        from ada.sections.profiles import profile_spec
    except ImportError as exc:  # pragma: no cover - only while the seam is unlanded
        raise CaeWriteError(
            "the CAE writer needs ada.sections.profiles.profile_spec (the section->profile "
            "mapping it shares with the Abaqus INP writer); it is not importable: {0}".format(exc)
        ) from exc
    return profile_spec


# --------------------------------------------------------------------------------------
# Guards 2, 3 and 4 — the refusals, and the one blessed source of a beam's endpoints
# --------------------------------------------------------------------------------------


def check_beam_shape(bm: Beam) -> None:
    """Guard 2: refuse a ``Beam`` subclass this writer cannot build, by exact type.

    ``isinstance`` is the wrong test on purpose. Every subclass *is* a ``Beam``, so an
    ``isinstance`` gate would wave them all through -- and a ``BeamRevolve`` emitted as the
    straight chord between its endpoints is a model that opens, meshes, solves and is wrong.

    A straight ``Beam`` and the three curved classes in :data:`CURVED_BEAM_TYPES` are built.
    Everything in :data:`REFUSED_BEAM_TYPES` is refused with the measurement that condemns
    it, and a class in neither list is refused for being unknown: the exactness of the test
    is what makes a fifth subclass added later a refusal rather than a silent chord.
    """
    from ada import Beam as StraightBeam

    if type(bm) is StraightBeam:
        return
    type_name = type(bm).__name__
    if type_name in CURVED_BEAM_TYPES:
        return
    reason = REFUSED_BEAM_TYPES.get(type_name)
    if reason is not None:
        raise UnsupportedBeamError("beam {0!r} is a {1}, which is refused: {2}.".format(bm.name, type_name, reason))
    raise UnsupportedBeamError(
        "beam {0!r} is a {1}, a Beam subclass this writer has never seen. It builds straight wires "
        "and the curved classes {2} and will not guess at anything else.".format(
            bm.name, type_name, ", ".join(CURVED_BEAM_TYPES)
        )
    )


def _eccentricity(bm: Beam) -> tuple[float, float, float] | None:
    """The member's constant offset from its end nodes, or ``None`` if it has none.

    Refuses a **varying** offset, which one section offset cannot express: ``*Beam Section
    Offset`` takes a single 2-tuple for the whole section. Measured across adapy's own Genie
    fixtures, every offset in the corpus is constant (7 in ``beams_constant_offset.xml``,
    2 in ``flush_top_varying_offset_types.xml``, 0 varying anywhere), so the constant case
    covers the models on hand and the varying one can wait for a model that needs it.
    """
    ends = []
    for label in ("e1", "e2"):
        ecc = getattr(bm, label)
        ends.append((0.0, 0.0, 0.0) if ecc is None else tuple(float(component) for component in ecc))
    e1, e2 = ends
    difference = max(abs(a - b) for a, b in zip(e1, e2))
    if difference > ECCENTRICITY_TOL:
        raise UnsupportedBeamError(
            "beam {0!r} has a VARYING offset -- e1 {1} against e2 {2}, differing by up to {3:g} length "
            "units. Abaqus expresses a beam offset as *Beam Section Offset, a single 2-tuple for the "
            "whole section, so one section cannot say 'this much at one end and that much at the "
            "other'. Expressing it would need the wire drawn through the offset endpoints (which moves "
            "the joints) or the extra nodes and *MPC BEAM links adapy's INP writer creates (which turns "
            "the offset into topology). Both are outside this writer, so a varying offset is refused "
            "rather than averaged.".format(bm.name, e1, e2, difference)
        )
    if max(abs(component) for component in e1) <= ECCENTRICITY_TOL:
        return None
    return e1


def beam_section_offset(bm: Beam, *, curved: bool = False) -> tuple[float, float] | None:
    """adapy's global ``e1``/``e2`` as Abaqus' ``beamSectionOffset``, or ``None``.

    Abaqus' offset is a 2-tuple in the section's own local axes, ``(along n1, along n2)``,
    while adapy's ``e1`` is a global vector -- so the conversion is a projection onto the
    member's own frame: ``n1`` is :func:`beam_n1` (the beam's ``yvec``, the same vector the
    INP writer emits) and ``n2`` is ``t x n1``, which is Abaqus' own definition of the
    second section axis.

    **The sign was measured, not derived.** ``getMassProperties()`` cannot settle it --
    probed on Abaqus 2025, a member's mass and centre of mass are *identical* for offsets
    ``(0, 0)``, ``(0.3, 0)``, ``(0, -0.4)`` and ``(0, 0.4)``, so CAE's own mass model ignores
    the offset entirely. The solver does not. A cantilever under an eccentric axial tip load
    was run three ways: adapy's existing INP route (the member drawn at ``node + e`` and tied
    back to the node line with ``*MPC BEAM``) against this route with the offset written
    ``+0.4`` and ``-0.4``, for ``e = (0, 0, -0.4)``, ``n1 = (0,1,0)``, ``n2 = (0,0,1)``::

        MPC, section at node + e     tip U = ( 4.1813789E-02, 0, -1.9071177E-01)  UR2 =  9.5355887E-02
        *Beam Section Offset  0.,-0.4      = ( 4.1813789E-02, 0, -1.9071177E-01)  UR2 =  9.5355887E-02
        *Beam Section Offset  0., 0.4      = ( 4.1813789E-02, 0,  1.9071177E-01)  UR2 = -9.5355887E-02
        *Beam Section Offset  0., 0.       = ( 3.6714338E-03, 0,  0.0           )  UR2 =  0.

    Identical to every digit printed for ``e.n2 = -0.4``, exactly sign-flipped for ``+0.4``,
    and no bending at all without an offset. So the projection is ``(e.n1, e.n2)`` with no
    negation, and it is pinned against adapy's own INP route rather than against a reading of
    Abaqus' convention.

    An **axial** component of ``e`` is refused: a section offset moves the section sideways
    and has no way to lengthen a member, so an axial offset would be dropped in silence.

    A **curved** member with an offset is refused too. ``(n1, n2)`` rotates along a curve
    while ``e`` is one global vector, so no single 2-tuple is the same offset at both ends;
    CAE accepts the pair (probed) and the result would be an offset that drifts out of the
    direction the model meant.
    """
    e1 = _eccentricity(bm)
    if e1 is None:
        return None
    if curved:
        raise UnsupportedBeamError(
            "beam {0!r} is curved AND carries an offset of {1}. Abaqus' *Beam Section Offset is stated "
            "in the section's local (n1, n2) axes, and those rotate along a curve while adapy's e1 is a "
            "single global vector -- so one 2-tuple cannot be the same offset at both ends of the arc. "
            "CAE does accept the combination (probed), which is why it is refused here rather than left "
            "to fail loudly somewhere else.".format(bm.name, e1)
        )
    offset = np.asarray(e1, dtype=float)
    n1 = np.asarray(beam_n1(bm), dtype=float)
    p1, p2 = beam_endpoints(bm)
    axis = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
    axis = axis / np.linalg.norm(axis)
    n2 = np.cross(axis, n1)
    axial = float(np.dot(offset, axis))
    if abs(axial) > ECCENTRICITY_TOL:
        raise UnsupportedBeamError(
            "beam {0!r} has an offset {1} with an AXIAL component of {2:g} along its own axis. A section "
            "offset displaces the cross-section sideways from the node line; it cannot move a member "
            "along itself, so that component has no expression in Abaqus at all and would be discarded "
            "without a word. Strip the axial part in the source model (adapy's Genie reader does exactly "
            "that for most offset containers) or split the member.".format(bm.name, e1, axial)
        )
    return (float(np.dot(offset, n1)), float(np.dot(offset, n2)))


#: The smallest ``sin`` of the angle between a curved member's tangent and its ``n1`` that
#: this writer will emit. ``method=N1_COSINES`` gives Abaqus **one** global vector for the
#: whole member, and Abaqus resolves the section's first axis by projecting it perpendicular
#: to each element's own tangent -- a projection whose length is exactly that ``sin``. A
#: straight member has one tangent, and :func:`beam_n1` already refuses a degenerate ``yvec``;
#: a curve's tangent turns, so ``n1`` can start well clear of it and end up along it.
#:
#: The floor is about sensitivity rather than about zero: the projected direction's error
#: under a perturbation of the tangent grows as ``1 / sin``, so at 0.1 a one-percent error in
#: the tangent is already a six-degree rotation of the profile. Below that the section's
#: orientation stops being a property of the model.
CURVE_N1_MIN_SIN = 0.1


def check_n1_holds_along_the_curve(bm: Beam, path) -> None:
    """Refuse a curved member whose ``n1`` lies along its own tangent somewhere.

    A failure mode a straight member cannot have, and the reason it matters is geometric
    rather than a quirk: ``N1_COSINES`` carries one vector for the member, so the profile's
    orientation at each element comes from projecting that vector perpendicular to the
    element's tangent. Where the two are parallel there is nothing left to project and the
    profile is turned whichever way the kernel happens to choose -- the "meshes, solves and is
    wrong" case, on the axis this writer's whole orientation story is about.
    """
    n1 = np.asarray(beam_n1(bm), dtype=float)
    points = np.asarray(path, dtype=float)
    tangents = points[1:] - points[:-1]
    lengths = np.linalg.norm(tangents, axis=1)
    tangents = tangents[lengths > 0.0] / lengths[lengths > 0.0][:, None]
    sines = np.linalg.norm(np.cross(tangents, n1), axis=1)
    worst = int(np.argmin(sines))
    if float(sines[worst]) < CURVE_N1_MIN_SIN:
        raise UnsupportedBeamError(
            "beam {0!r} is curved, and its n1 {1} comes within sin={2:.3g} of its own tangent {3} near "
            "{4}. Abaqus is given one n1 for the whole member (method=N1_COSINES) and projects it "
            "perpendicular to each element's tangent, so where the two line up there is nothing left to "
            "project and the profile is turned an arbitrary way round -- a model that meshes and solves "
            "and is wrong. Give the beam an explicit 'up' that stays clear of its tangent along the whole "
            "curve, or split it where the tangent turns past n1.".format(
                bm.name,
                tuple(float(c) for c in n1),
                float(sines[worst]),
                tuple(round(float(c), 6) for c in tangents[worst]),
                tuple(round(float(c), 6) for c in points[worst]),
            )
        )


def beam_endpoints(bm: Beam) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Guard 4: a beam's endpoints, from :meth:`ada.Beam.axis_global` and nowhere else.

    Its docstring is explicit that exporters share it so they cannot disagree about
    where a beam is: the nodes are expressed in the beam's own frame, so a beam inside
    a placed :class:`~ada.Part` has to be pushed through the accumulated placement.
    Reading ``n1.p``/``n2.p`` here would drop that placement.

    The coordinates come out in the model's own units, unscaled -- see the module
    docstring on why ``unit_scale`` is refused rather than applied.
    """
    p1, p2 = bm.axis_global()
    return (
        tuple(float(c) for c in p1),
        tuple(float(c) for c in p2),
    )


def beam_n1(bm: Beam) -> tuple[float, float, float]:
    """Abaqus' ``n1`` for this beam: ``beam.yvec``, the same vector the INP writer emits.

    The INP writer's beam-section data line ends with ``fem_sec.local_y``, which is the
    beam's ``yvec``. Deriving it the same way here is what stops a *third* orientation
    convention appearing, which is the drift that would actually happen.
    """
    yvec = np.asarray(bm.yvec, dtype=float)
    norm = float(np.linalg.norm(yvec))
    if norm <= 0.0:
        raise CaeWriteError("beam {0!r} has a degenerate local y-vector {1}".format(bm.name, tuple(yvec)))
    unit = yvec / norm
    return (float(unit[0]), float(unit[1]), float(unit[2]))


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


def _round_tolerance(value: float) -> float:
    """A tolerance, rounded to a few significant digits.

    Applied to the cylinder's radius and overshoot, never to a coordinate: these two are
    tolerances, so 0.0003 is as good as 0.00030000000000000003 and much easier to read in
    the emitted script.
    """
    return float("%.6g" % value)


def _cylinder(p1, p2, length: float):
    """The bounding cylinder that locates one member, its end caps overshot slightly."""
    start = np.asarray(p1, dtype=float)
    end = np.asarray(p2, dtype=float)
    direction = (end - start) / length
    overshoot = _round_tolerance(length * CYLINDER_OVERSHOOT_FRACTION)
    radius = _round_tolerance(length * CYLINDER_RADIUS_FRACTION)
    c1 = start - direction * overshoot
    c2 = end + direction * overshoot
    return tuple(float(x) for x in c1), tuple(float(x) for x in c2), radius


def _material_row(mat: Material, cae_name: str) -> _MaterialRow:
    model = mat.model
    # Abaqus rejects a zero density; the INP writer already substitutes 1e-6, so the two
    # Abaqus paths agree on what a density-less material becomes.
    density = float(model.rho) if model.rho and float(model.rho) > 0.0 else 1e-06
    return _MaterialRow(
        cae_name=cae_name,
        young=float(model.E),
        poisson=float(model.v),
        density=density,
        shear=float(model.G),
    )


#: The one CAE profile class that is not defined by lengths. ``GeneralizedProfile`` takes
#: an area and second moments, which is where adapy's GENERAL sections land — and, since
#: Abaqus' solver rejects the ``section=CHANNEL`` that CAE itself writes, where a channel
#: lands on the INP side too. It differs from every other profile class twice over: its
#: arguments scale as L², L⁴ and L⁴ rather than as L, and its section has to integrate
#: ``BEFORE_ANALYSIS`` with its own elastic constants. Both are handled by name, because
#: both come from the same fact about the class.
_NON_LINEAR_PROFILE_CLASS = "GeneralizedProfile"


def check_unit_scale(unit_scale: float) -> None:
    """Refuse anything but ``1.0``, and say why the parameter is not simply fixed.

    The implementation this replaces multiplied every coordinate and every profile
    dimension by ``unit_scale`` and left the *material* alone. Verified:
    ``unit_scale=1000`` wrote ``model.IProfile(..., h=300.0)`` alongside
    ``Elastic(table=((210000000000.0, 0.3),))`` — a model whose geometry is in
    millimetres and whose Young's modulus is in pascals, so every stiffness in it is out
    by 10⁶, and not one guard anywhere said a word. That is the exact class of
    plausible-but-wrong output this writer exists to refuse, produced by the writer
    itself.

    It cannot be repaired by scaling the material too, because there is no single factor
    to scale it by. With lengths multiplied by ``s``, ``E`` scales as ``s⁻²`` and density
    as ``s⁻³`` **only once a force or mass unit has been chosen** — Abaqus in
    N/mm/s wants tonne/mm³, so a metre-to-millimetre conversion multiplies density by
    1e-12 rather than by ``s⁻³`` = 1e-9. A unit system is three independent choices, not
    one number, so converting a model belongs to the model.
    """
    if float(unit_scale) != 1.0:
        raise CaeWriteError(
            "unit_scale={0!r} is refused. It used to multiply coordinates and profile dimensions "
            "while leaving E and the density untouched, which emitted a millimetre model carrying "
            "a Young's modulus in pascals -- every stiffness out by a factor of a million, with "
            "nothing reporting it. No single factor can fix that: E scales as s**-2 and density as "
            "s**-3 only once a force/mass unit has been chosen (Abaqus in N/mm/s wants tonne/mm3, "
            "so density scales by 1e-12, not 1e-9, from m to mm). Convert the model to the units "
            "you want -- Part.units, or a unit conversion on the assembly -- and write it with "
            "unit_scale=1.0.".format(unit_scale)
        )


#: CAE sets live inside a part, so two parts *could* each hold a set called ``bm1``.
#: This writer still keeps one global scope for them, for two reasons: the result
#: sidecar keys ``edges_per_member`` by set name, so a duplicate there would silently
#: overwrite one member's edge count with another's; and a name that is unique across
#: the whole script is the one a user can trace from a CAE result back to a GeniE
#: member without also knowing which part it came from.
_SET_SCOPE = "sets"


def _offset_suffix(offset: tuple[float, float] | None) -> str:
    """A traceable, CAE-legal name fragment for a section offset.

    A reader has to be able to tell which section carries which offset, so the numbers go
    into the name rather than a counter -- but CAE rejects a dot outright (measured), and a
    minus sign in a name is legal but reads as an operator. So ``(0.0, -0.4)`` becomes
    ``_off_0_m0p4``, at six significant figures for readability. Two offsets that agree to
    six figures would collide, which :func:`build_plan` checks for rather than trusting.
    """
    if offset is None:
        return ""
    parts = []
    for value in offset:
        text = "%.6g" % float(value)
        parts.append(text.replace("-", "m").replace(".", "p").replace("+", ""))
    return "_off_{0}".format("_".join(parts))


def _part_topology(part_plan: _PartPlan, joint_tol: float) -> PartTopology:
    """The topology one part must build, and the refusal of the one case that cannot be stated.

    **Crossing policy: a model with an interior crossing is refused, not asserted.**

    CAE imprints a crossing exactly as it imprints a real T-joint -- measured, an X of two
    4 m members builds ``edges=4 vertices=5`` -- so the emitted model would carry a shared
    vertex, and therefore a moment-transferring connection, at a point where the adapy
    model has no joint at all. Two braces passing each other in an X is the ordinary case
    of that, and the ordinary truth about it is that they are *not* connected.

    Asserting the crossing instead of refusing it would make the change *known*, which is
    what the alternative buys, but it would not make it *right*: the guard would then
    certify a model stiffer than its source. There is also no way to have it both ways in
    one part, because ``mergeType`` is per-``WirePolyLine`` and not per-pair -- the only
    settings available are "merge every touching member" (which welds the crossing) and
    ``SEPARATE`` (which disconnects the real joints too). A model that needs the
    distinction is therefore outside what this writer can express, and its standing
    rule for that is to refuse rather than approximate: the same rule that refuses a
    ``BeamRevolve`` and a 0.66 m eccentricity.

    The refusal is a *plan-time* geometric test and the in-kernel topology guard is its
    backstop: a crossing this misses -- because the two members pass within CAE's 1e-6
    merge tolerance but further apart than ``joint_tol``, say -- reappears in CAE as
    sub-edges adapy did not predict, and fails the build there. So a crossing is never
    unasserted; it is refused if it can be seen here and reported if it cannot.
    """
    segments = [Segment(name=m.cae_set_name, p1=m.p1, p2=m.p2, points=m.path) for m in part_plan.members]
    crossings = find_crossings(segments, joint_tol)
    if crossings:
        on_curve = [crossing for crossing in crossings if crossing.kind == "on_curve"]
        extra = (
            " A contact on a curved member is refused for a second reason on top of the first: where "
            "along a spline CAE would put an imprinted vertex is the spline's own parameterisation's "
            "business, so that member's sub-edge count cannot be stated from anything adapy holds -- "
            "and this writer asserts every member's sub-edge count. Measured: a straight wire landing on "
            "the midpoint of a spline wire took the part from 2 edges / 3 vertices to 3 edges / 4 "
            "vertices, so the split is real and not hypothetical."
            if on_curve
            else ""
        )
        raise CaeWriteError(
            "part {0!r} contains {1} member pair(s) that meet away from their ends, and the CAE "
            "writer refuses them: {2}. CAE imprints such a meeting into a shared vertex "
            "exactly as it does a real joint (measured: an X of two members builds 4 edges and 5 "
            "vertices), so the emitted model would transfer moment at a point where this model has "
            "no joint -- structural connectivity the source never had. mergeType is per-wire and not "
            "per-pair, so 'connect the real joints but not this crossing' cannot be expressed in one "
            "CAE part at all. Split the members at the crossing in the source model if the "
            "connection is intended, or put them in separate parts if it is not.{3}".format(
                part_plan.part_name,
                len(crossings),
                "; ".join(crossing.describe() for crossing in crossings),
                extra,
            )
        )
    return expected_topology(segments, joint_tol)


def build_plan(root: Part, model_name: str = "Model-1", unit_scale: float = 1.0) -> _Plan:
    """Resolve the whole emission before any text is written.

    Every refusal happens here, so a model that cannot be expressed leaves no
    half-written script behind.
    """
    profile_spec = _load_profile_spec()
    check_unit_scale(unit_scale)

    plan = _Plan(
        root_name=root.name,
        model_name=model_name,
        units=str(getattr(getattr(root, "units", ""), "value", getattr(root, "units", ""))),
        # adapy's own definition of "these two points are the same point": the tolerance
        # its FEM node container merges nodes at and its Connections.find() looks for
        # joints with. Read from the config rather than hardcoded, so a model written with
        # a tightened point_tol gets a topology stated at that same tolerance.
        joint_tol=float(Config().general_point_tol),
    )
    plan.registries = {
        "parts": NameRegistry("parts"),
        "instances": NameRegistry("assembly instances"),
        "materials": NameRegistry("materials"),
        "profiles": NameRegistry("profiles"),
        "sections": NameRegistry("sections"),
        _SET_SCOPE: NameRegistry(_SET_SCOPE),
    }
    materials_seen: dict[str, _MaterialRow] = {}
    profiles_seen: dict[str, tuple[str, dict]] = {}
    sections_seen: dict[str, _SectionUse] = {}

    # Sorted, and sorted again inside each part: WS-C's golden files depend on the
    # emission being a function of the model alone, not of dict insertion order.
    for part in sorted(root.get_all_subparts(include_self=True), key=lambda p: p.name):
        untranslated = list(part.plates) + list(part.pipes) + list(part.walls) + list(part.shapes) + list(part.masses)
        for other in sorted(untranslated, key=lambda o: (type(o).__name__, o.name)):
            plan.skipped.append(
                SkippedObject(
                    name=other.name,
                    kind=type(other).__name__,
                    reason=(
                        "the CAE writer translates beams only; this object is absent from the emitted "
                        "model rather than approximated by one"
                    ),
                )
            )

        beams = sorted(part.beams, key=lambda b: b.name)
        if not beams:
            continue

        part_plan = _PartPlan(
            part_name=part.name,
            cae_part_name=plan.registries["parts"].allocate_unique(part.name),
            cae_instance_name=plan.registries["instances"].allocate_unique("{0}-1".format(part.name)),
        )
        set_names = plan.registries[_SET_SCOPE]

        for bm in beams:
            check_beam_shape(bm)

            p1, p2 = beam_endpoints(bm)
            length = float(np.linalg.norm(np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)))
            if length <= MIN_BEAM_LENGTH:
                raise CaeWriteError(
                    "beam {0!r} has zero length ({1} -> {2}); a wire needs two distinct "
                    "points.".format(bm.name, p1, p2)
                )

            curved = is_curved_beam_type(bm)
            path = None
            curve_length = None
            if curved:
                try:
                    path, curve_length = sample_member_curve(bm, p1, p2, plan.joint_tol)
                except CurveNotSupported as exc:
                    raise UnsupportedBeamError(
                        "beam {0!r} is a {1} whose axis cannot be drawn as one spline wire: {2}.".format(
                            bm.name, type(bm).__name__, exc
                        )
                    ) from exc
            offset = beam_section_offset(bm, curved=curved)
            if curved:
                check_n1_holds_along_the_curve(bm, path)

            mat = bm.material
            cae_material_name = plan.registries["materials"].allocate_shared(mat.name)
            row = _material_row(mat, cae_material_name)
            previous_row = materials_seen.get(cae_material_name)
            if previous_row is not None and previous_row != row:
                raise CaeNameError(
                    "two different materials are both named {0!r} ({1} vs {2}); CAE would keep "
                    "only one of them".format(mat.name, previous_row.describe(), row.describe())
                )
            materials_seen[cae_material_name] = row

            sec: Section = bm.section
            try:
                spec = profile_spec(sec)
            except NotImplementedError as exc:
                # The shared mapping refuses a section it cannot express faithfully -- a
                # POLY, for instance, whose CAE counterpart turned out to be thin-walled
                # (measured 0.006 m3 against 0.040 m3 for the filled equivalent). Name the
                # beam it came from, or the user is left with a type name and no member.
                raise CaeWriteError(
                    "beam {0!r} carries section {1!r} ({2}), which has no faithful Abaqus "
                    "cross-section: {3}".format(bm.name, sec.name, sec.type, exc)
                ) from exc
            cae_profile_name = plan.registries["profiles"].allocate_shared(sec.name)
            profile_entry = (spec.cae_class, dict(spec.cae_kwargs))
            previous_profile = profiles_seen.get(cae_profile_name)
            if previous_profile is not None and previous_profile != profile_entry:
                raise CaeNameError(
                    "two different sections are both named {0!r} ({1} vs {2}); CAE would keep only "
                    "one of them".format(sec.name, previous_profile, profile_entry)
                )
            profiles_seen[cae_profile_name] = profile_entry

            # One CAE BeamSection per (profile, material, offset): a BeamSection binds all
            # three, so the same profile in two steel grades -- or at two offsets -- is two
            # sections.
            cae_section_name = plan.registries["sections"].allocate_shared(
                "sec_{0}_{1}{2}".format(cae_profile_name, cae_material_name, _offset_suffix(offset))
            )
            previous_use = sections_seen.get(cae_section_name)
            if previous_use is not None and previous_use.offset != offset:
                # The suffix rounds to six significant figures for readability, so two
                # offsets that differ below that would share a name and CAE would keep only
                # one of the sections. Vanishingly unlikely and silently wrong, so checked.
                raise CaeNameError(
                    "two different section offsets both name the section {0!r} ({1} vs {2}); CAE would "
                    "keep only one of them".format(cae_section_name, previous_use.offset, offset)
                )
            sections_seen.setdefault(
                cae_section_name,
                _SectionUse(
                    cae_section_name=cae_section_name,
                    cae_profile_name=cae_profile_name,
                    cae_material_name=cae_material_name,
                    spec=spec,
                    section_name=sec.name,
                    offset=offset,
                ),
            )

            member = _MemberPlan(
                beam_name=bm.name,
                cae_set_name=set_names.allocate_unique(bm.name),
                cae_section_name=cae_section_name,
                p1=p1,
                p2=p2,
                n1=beam_n1(bm),
            )
            if curved:
                member.path = path
                member.curve_length = curve_length
            else:
                member.cyl1, member.cyl2, member.radius = _cylinder(p1, p2, length)
            part_plan.members.append(member)

        part_plan.topology = _part_topology(part_plan, plan.joint_tol)
        plan.parts.append(part_plan)

    if not plan.parts:
        raise CaeWriteError(
            "nothing to write: {0!r} holds no beams, and the CAE writer translates beams only.".format(root.name)
        )

    plan.materials = [materials_seen[name] for name in sorted(materials_seen)]
    plan.sections = [sections_seen[name] for name in sorted(sections_seen)]
    return plan


# --------------------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------------------


def _num(value: float) -> str:
    """A float literal that round-trips, so the script says exactly what adapy meant."""
    return repr(float(value))


def _pt(point) -> str:
    return "({0})".format(", ".join(_num(c) for c in point))


#: The two CAE keywords that displace a beam's cross-section from its node line, and which
#: kind of section each belongs to. They are not interchangeable, and that is measured:
#:
#: * a **shaped** profile takes ``beamSectionOffset``, which CAE writes as
#:   ``*Beam Section Offset``;
#: * a **generalized** profile -- ``integration=BEFORE_ANALYSIS``, where a channel has to
#:   land on both Abaqus routes -- **refuses** it. ``BeamSection(..., beamSectionOffset=...)``
#:   and ``sections[x].setValues(beamSectionOffset=...)`` both raise
#:   ``TypeError: keyword error on beamSectionOffset``, although the attribute exists and
#:   reads back ``[0.0, 0.0]``. What such a section does take is ``centroid``, which CAE
#:   writes as ``*Centroid``.
#:
#: Both were pinned against adapy's own ``*MPC BEAM`` route by the solver, and both reproduce
#: it exactly: an eccentric axial tip load on a cantilever gave
#: ``U = (2.2754887E-01, 0, -1.1042098E+00)`` with ``UR2 = 5.5210490E-01`` for the MPC model
#: and for ``*Centroid 0., -0.4`` alike, and exactly sign-flipped for ``+0.4``. So the
#: projection and its sign are the same for both keywords; only the spelling differs.
_OFFSET_KEYWORD = "beamSectionOffset"
_GENERALIZED_OFFSET_KEYWORD = "centroid"


def _offset_keyword(use: _SectionUse) -> str:
    return _GENERALIZED_OFFSET_KEYWORD if use.spec.cae_class == _NON_LINEAR_PROFILE_CLASS else _OFFSET_KEYWORD


def _offset_argument(use: _SectionUse) -> str:
    """The offset argument for a ``BeamSection`` call, or nothing at all.

    Omitted rather than written as ``(0.0, 0.0)`` when there is no offset: a model with no
    eccentricity emits exactly what it emitted before this feature existed.
    """
    if use.offset is None:
        return ""
    return ", {0}=({1}, {2})".format(_offset_keyword(use), _num(use.offset[0]), _num(use.offset[1]))


def _bounding_boxes(plan: _Plan) -> dict[str, tuple[tuple, tuple]]:
    boxes = {}
    for part_plan in plan.parts:
        points = np.asarray([m.p1 for m in part_plan.members] + [m.p2 for m in part_plan.members], dtype=float)
        boxes[part_plan.cae_part_name] = (
            tuple(float(x) for x in points.min(axis=0)),
            tuple(float(x) for x in points.max(axis=0)),
        )
    return boxes


def _bbox_tolerance(boxes: dict) -> float:
    diagonal = 0.0
    for low, high in boxes.values():
        diagonal = max(diagonal, float(np.linalg.norm(np.asarray(high) - np.asarray(low))))
    return max(1e-06, diagonal * 1e-07)


def _header(plan: _Plan, result_name: str, adapy_version: str) -> list[str]:
    beams = sum(len(p.members) for p in plan.parts)
    curved = sum(1 for p in plan.parts for m in p.members if m.is_curved)
    offsets = sum(1 for use in plan.sections if use.offset is not None)
    lines = [
        "# -*- coding: utf-8 -*-",
        "# Abaqus/CAE concept model, written by adapy {0}.".format(adapy_version),
        "#",
        "# adapy source part : {0}".format(plan.root_name),
        "# CAE model         : {0}".format(plan.model_name),
        "# adapy units       : {0}".format(plan.units),
        "#                     Every coordinate and every profile dimension below is in those",
        "#                     units, unscaled: unit_scale must be 1.0. It used to multiply both",
        "#                     and leave E and the density alone, which emitted a millimetre model",
        "#                     with a modulus in pascals and nothing to report it.",
        "# joint tolerance   : {0}".format(_num(plan.joint_tol)),
        "#                     adapy's Config().general_point_tol -- how close two members have to",
        "#                     be for adapy to call them joined, and so what EXPECTED_TOPOLOGY",
        "#                     below is stated at. CAE's own merge tolerance is {0}.".format(_num(CAE_MERGE_TOL)),
        "# parts             : {0}".format(len(plan.parts)),
        "# beams             : {0}".format(beams),
        "# materials         : {0}".format(len(plan.materials)),
        "# beam sections     : {0}".format(len(plan.sections)),
        "#",
    ]
    # Said only when there is something to say: a straight, offset-free model's header reads
    # exactly as it did before curves and offsets existed.
    if curved:
        lines[-3:-3] = [
            "#                     {0} of them curved, drawn as WireSpline through points on their".format(curved),
            "#                     exact curve -- a BeamCurved's ngeom spline, a BeamRevolve's arc.",
        ]
    if offsets:
        lines += [
            "# {0} of those beam sections carry a beam offset, projected from adapy's global e1 onto the".format(
                offsets
            ),
            "# section's own (n1, n2) axes. Abaqus writes one as '*Beam Section Offset', or as",
            "# '*Centroid' for a generalized section, which refuses the other keyword.",
            "#",
        ]
    if plan.skipped:
        lines += [
            "# NOT translated by this writer. These objects are absent from the model",
            "# built below; they are listed here, and in the result sidecar, so their absence is",
            "# visible rather than silent:",
        ]
        lines += ["#   {0} {1!r}".format(s.kind, s.name) for s in sorted(plan.skipped, key=lambda s: (s.kind, s.name))]
        lines.append("#")
    else:
        lines += ["# Every physical object in the source part was translated.", "#"]
    lines += [
        "# Run:  abaqus cae noGUI=<this file>",
        "# 'print' does not reach stdout under CAE: it lands in abaqus.rpy prefixed '#: '.",
        "#",
        "# Machine-readable outcome: {0}, written next to this script on".format(result_name),
        "# success AND on failure. Read it, and do not read the launcher's exit status:",
        "# measured on Abaqus 2025, abq<ver>.bat returns 0 even when the cae process it",
        "# started died with a non-zero status (CAE does print 'Abaqus Error: cae exited",
        '# with an error\' in that case). A failed run leaves "ok": false and a reason.',
        "#",
        "# Written with .format() and no f-strings, so an older Abaqus kernel reports a real",
        "# error rather than a SyntaxError.",
        "",
    ]
    return lines


_PREAMBLE = """from __future__ import print_function

import json
import os
import sys
import traceback

from abaqus import *
from abaqusConstants import *

# Mandatory, not decorative: without caeModules, mdb has no geometry importers at all
# (measured -- "'Mdb' object has no attribute 'openAcis'").
from caeModules import *
"""


_RUNTIME_HELPERS = '''
def _result_path():
    """Next to this script, so the sidecar travels with the artefact that produced it."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = os.getcwd()
    return os.path.join(here, RESULT_NAME)


def _write_result():
    handle = open(_result_path(), 'w')
    try:
        handle.write(json.dumps(_RESULT, indent=2, sort_keys=True))
    finally:
        handle.close()


def _fail(message):
    """Guard 5: CAE can exit 0 on a half-built model, so record AND signal.

    Measured on Abaqus 2025, and it is not what it looks like: a SystemExit leaving a
    `cae noGUI=` script does NOT reach the process exit status. `sys.exit(1)` here left
    the run reporting 0 -- exactly the "it ran" versus "it built a third of the model"
    confusion this guard exists to remove. os._exit does reach the caller, so the
    sidecar and the reason are written and flushed first and the status is forced after.
    """
    _RESULT['ok'] = False
    _RESULT['errors'].append(message)
    _write_result()
    banner = 'ADAPY-CAE BUILD FAILED: ' + message
    print(banner)
    # print() under CAE lands in abaqus.rpy prefixed '#: ', not on the console, so say it
    # again on every stream that might actually be one.
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            stream.write(banner + '\\n')
            stream.flush()
        except Exception:
            pass
    os._exit(1)


def _model():
    if MODEL_NAME in mdb.models.keys():
        model = mdb.models[MODEL_NAME]
        model.setValues(description=MODEL_DESCRIPTION)
        return model
    return mdb.Model(name=MODEL_NAME, description=MODEL_DESCRIPTION)


def _guard_no_name_collisions(model):
    """Guard 7: nothing this script is about to create may already exist.

    CAE does not raise when a name is reused. Measured: it silently REPLACES the object
    and invalidates every handle to the old one, which then fails much later with
    "AccessError: ... no longer exists" -- or does not fail at all. So running this script
    twice in one GUI session would quietly swap every part, section and profile, and the
    second run would look exactly as clean as the first.

    That is why this cannot be left to the kernel and has to happen before a single object
    is built: once the first Part is replaced, the damage is done and there is nothing to
    abort back to.
    """
    present = {
        'parts': model.parts.keys(),
        'materials': model.materials.keys(),
        'profiles': model.profiles.keys(),
        'sections': model.sections.keys(),
        'instances': model.rootAssembly.instances.keys(),
    }
    clashes = []
    for kind in sorted(PLANNED_NAMES.keys()):
        existing = list(present[kind])
        for name in PLANNED_NAMES[kind]:
            if name in existing:
                clashes.append('{0} {1!r}'.format(kind, name))
    if clashes:
        _fail('model {0!r} already holds {1} of the object(s) this script would create, and CAE '
              'replaces an object whose name is reused rather than refusing it -- so building here '
              'would silently swap them: {2}. Delete that model, or write the script again with a '
              'model_name of its own.'.format(MODEL_NAME, len(clashes), ', '.join(clashes)))


def _guard_topology(model):
    """Guard 6: the ONLY check that can tell a connected frame from a pile of loose sticks.

    Guard 1 below cannot, although it was written believing it could. Measured, with this
    script's own wire calls:

        T-joint, IMPRINT (correct)        edges=3 vertices=4     guard 1 passes
        T-joint, SEPARATE (disconnected)  edges=2 vertices=4     guard 1 PASSES
        brace 1e-6 off the girder line    edges=2 vertices=4     guard 1 PASSES
        collinear pair, touching          edges=2 vertices=3     guard 1 passes
        collinear pair, 1e-6 apart        edges=2 vertices=4     guard 1 PASSES

    Every one of those has "every edge carries exactly one section", so the guard that
    counts sections cannot see connectivity at all. Note the last pair especially: two
    members that failed to join do not change the EDGE count either. Only the vertex count
    moves, which is why it is checked here.

    EXPECTED_TOPOLOGY was computed from the adapy model -- every member's endpoints were
    known there -- so this compares the kernel against an independent statement of what it
    should have built, not against its own bookkeeping.
    """
    problems = []
    for part_name in sorted(EXPECTED_TOPOLOGY.keys()):
        expected = EXPECTED_TOPOLOGY[part_name]
        part = model.parts[part_name]
        built_edges = len(part.edges)
        built_vertices = len(part.vertices)
        record = _RESULT['guards'].setdefault(part_name, {})
        record['expected_edges'] = expected['edges']
        record['expected_vertices'] = expected['vertices']
        record['built_vertices'] = built_vertices
        disagree = []
        for set_name in sorted(expected['edges_per_member'].keys()):
            wanted = expected['edges_per_member'][set_name]
            built = _RESULT['edges_per_member'].get(set_name)
            if built != wanted:
                disagree.append('member {0!r} expected {1} sub-edge(s), CAE built {2}'.format(
                    set_name, wanted, built))
        if disagree:
            problems.append('part {0!r}: {1}'.format(part_name, '; '.join(disagree)))
        if built_edges != expected['edges']:
            problems.append('part {0!r}: adapy described {1} edge(s), CAE built {2}'.format(
                part_name, expected['edges'], built_edges))
        if built_vertices != expected['vertices']:
            problems.append('part {0!r}: adapy described {1} vertex(es), CAE built {2}'.format(
                part_name, expected['vertices'], built_vertices))
    if problems:
        _fail('the topology CAE built is not the topology adapy described -- ' + ' | '.join(problems)
              + ' | FEWER sub-edges or MORE vertices than expected means a joint did not merge: '
              'CAE merges two points only when they are closer than ' + repr(CAE_MERGE_TOL)
              + ' in model units, while adapy treats anything within ' + repr(JOINT_TOL)
              + ' as one point, so a member landing between those two distances is joined in the '
              'adapy model and loose here. MORE sub-edges or FEWER vertices means CAE imprinted a '
              'connection adapy does not model -- two members crossing away from their ends, which '
              'the writer refuses when it can see it. Either way the geometry is reported, never '
              'snapped: fix the model.')


def _member_edges(part_name, set_name, edges):
    """Check and record what the cylinder on the preceding line actually found.

    Each member is located by a bounding cylinder, never by findAt at a midpoint: a
    member landing mid-span SPLITS the through member (measured: 1 edge -> 3).
    getByBoundingCylinder returns every sub-edge while findAt returns only the one
    containing the point, so a midpoint lookup would section half a beam and leave the
    other half bare. So a member legitimately yields more than one edge here -- but
    never zero, because its own wire was drawn a few lines up.
    """
    if len(edges) == 0:
        _fail('member {0!r} of part {1!r}: the bounding cylinder on the line above contains no '
              'edge, although the member\\'s own wire was drawn. Either the cylinder is too tight '
              'for this model\\'s scale, or the geometry is not where it was drawn.'.format(
                  set_name, part_name))
    _RESULT['edges_per_member'][set_name] = len(edges)
    return edges


def _guard_every_edge_sectioned(model):
    """Guard 1: every edge carries exactly one section, and every section an orientation.

    Imprinting can leave a sub-edge no cylinder claimed, and a duplicated wire is an edge
    no cylinder claims either. Neither shows up anywhere else.

    This guard used to claim it caught connectivity as well. It does not, and the
    measurements are in _guard_topology above: a disconnected frame reaches this check
    with every one of its edges sectioned. Do not delete that guard on the strength of
    this one.

    Read back from the kernel's own sectionAssignments and the kernel's own sets -- not
    from this script's bookkeeping -- so a bug in the bookkeeping cannot make the guard
    pass. (Measured: sa.region is a tuple whose first element is the set's name.)
    """
    problems = []
    for part_name in sorted(_RESULT['created']['parts']):
        part = model.parts[part_name]
        covered = {}
        for assignment in part.sectionAssignments:
            set_name = assignment.region[0]
            if set_name not in part.sets.keys():
                problems.append('part {0!r}: section assignment region {1!r} is not one of the '
                                "part's sets, so its coverage cannot be verified".format(
                                    part_name, assignment.region))
                continue
            for edge in part.sets[set_name].edges:
                covered.setdefault(edge.index, []).append(set_name)
        all_indices = set([edge.index for edge in part.edges])
        unassigned = sorted(all_indices - set(covered.keys()))
        doubled = sorted([index for index in covered if len(covered[index]) > 1])
        orientations = len(part.beamSectionOrientations)
        # update(), not assignment: _guard_topology has already recorded what adapy expected
        # for this part, and overwriting it would drop the connectivity verdict from the
        # sidecar -- the one number a reader most wants next to 'edges'.
        _RESULT['guards'].setdefault(part_name, {}).update({
            'edges': len(all_indices),
            'edges_with_a_section': len(covered),
            'edges_with_no_section': len(unassigned),
            'edges_with_two_sections': len(doubled),
            'section_assignments': len(part.sectionAssignments),
            'orientations': orientations,
            'sets': len(part.sets.keys()),
        })
        _RESULT['created']['section_assignments'] += len(part.sectionAssignments)
        _RESULT['created']['orientations'] += orientations
        for set_name in sorted(part.sets.keys()):
            _RESULT['created']['sets'].append(set_name)
        # A section without an orientation is a beam whose profile is turned an unknown
        # way round -- it meshes and solves, and its weak axis is wherever CAE defaulted.
        if orientations < len(part.sectionAssignments):
            problems.append('part {0!r}: {1} section assignments but only {2} beam orientations, so at '
                            'least one member\\'s profile is turned an unknown way round'.format(
                                part_name, len(part.sectionAssignments), orientations))
        if not all_indices:
            problems.append('part {0!r} has no edges at all: no geometry was built'.format(part_name))
            continue
        if unassigned:
            problems.append('part {0!r}: {1} of {2} edges carry a section assignment; edges {3} carry '
                            'none (pointOn {4})'.format(part_name, len(covered), len(all_indices), unassigned,
                                                        [part.edges[i].pointOn for i in unassigned[:8]]))
        if doubled:
            problems.append('part {0!r}: edges {1} carry more than one section assignment, so which '
                            'section applies is ambiguous'.format(part_name, doubled))
    if problems:
        _fail('every edge must end with exactly one section assignment -- ' + ' | '.join(problems))


def _guard_bounding_box(model):
    """A jacket built 1000x too small, or a Part placement dropped on the way out.

    adapy computed these corners from Beam.axis_global() in the emitted units, so a
    mismatch means the script built something adapy did not describe.
    """
    problems = []
    for part_name in sorted(EXPECTED_BBOX.keys()):
        part = model.parts[part_name]
        if len(part.vertices) == 0:
            problems.append('part {0!r} has no vertices'.format(part_name))
            continue
        low = [None, None, None]
        high = [None, None, None]
        for vertex in part.vertices:
            point = vertex.pointOn[0]
            for axis in range(3):
                if low[axis] is None or point[axis] < low[axis]:
                    low[axis] = point[axis]
                if high[axis] is None or point[axis] > high[axis]:
                    high[axis] = point[axis]
        expected_low, expected_high = EXPECTED_BBOX[part_name]
        worst = 0.0
        for axis in range(3):
            worst = max(worst, abs(low[axis] - expected_low[axis]), abs(high[axis] - expected_high[axis]))
        _RESULT['guards'].setdefault(part_name, {})['bbox_error'] = worst
        if worst > BBOX_TOL:
            problems.append('part {0!r}: built bounding box {1} {2}, but adapy described {3} {4} '
                            '(worst axis off by {5!r}, tolerance {6!r})'.format(
                                part_name, tuple(low), tuple(high), expected_low, expected_high, worst, BBOX_TOL))
    if problems:
        problems.append('a coordinate off by this much is a unit slip or a lost Part placement, not noise')
        _fail('the built geometry is not where adapy said it is -- ' + ' | '.join(problems))


'''


#: Emitted only when the model HAS a curved member. A script that carries a helper nothing
#: calls invites the next reader to wonder which members use it; a straight, offset-free model
#: emits exactly what it emitted before curves existed.
_CURVED_MEMBER_HELPER = '''
def _curved_member_edges(part_name, set_name, part, points, expected_length):
    """Locate a curved member, and prove the wire CAE built is the curve adapy described.

    A bounding cylinder cannot do this job, and that is measured rather than assumed: a
    cylinder round the chord of a quarter arc of radius 2 returned **0 edges**, because
    getByBoundingCylinder returns only fully contained edges and the arc bulges 0.59 units
    outside its own chord. Widening the cylinder until it holds the arc makes it wide enough
    to swallow every neighbour.

    So the curve is located by findAt at every interior sample point, which is the call the
    straight path deliberately does not use -- findAt returns only the sub-edge containing
    the point, so on a member that might be SPLIT it would section part of it and leave the
    rest bare. A curved member cannot be split here, because the writer refuses any model in
    which something touches a curve away from its ends; if that refusal ever misses one, the
    assertion below sees two different edges, and the part's own edge total sees it too.

    Three things are asserted, and each is a different failure:

    * every interior sample point lies on some edge -- findAt warns and returns an empty
      sequence otherwise (measured: 1.4e-3 off the curve was already "could not find a
      geometric entity"), so this is a real check that the spline passes through the points;
    * they all lie on the SAME edge -- one member, one wire;
    * that edge's arc length matches the length adapy sampled. A spline through points on a
      curve is an interpolation, not the curve, and this is the only number that says how
      good the interpolation was. Measured relative error at the emitted sampling: 2.6e-06,
      against a chord of the same arc which is 9.9% short.
    """
    indices = []
    for point in points[1:-1]:
        found = part.edges.findAt((point,))
        if len(found) == 0:
            _fail('member {0!r} of part {1!r} is curved, and the sample point {2} it was drawn through '
                  'lies on no edge at all. The spline CAE built is not the curve adapy sampled.'.format(
                      set_name, part_name, point))
        for edge in found:
            if edge.index not in indices:
                indices.append(edge.index)
    if len(indices) != 1:
        _fail('member {0!r} of part {1!r} is curved and its own sample points lie on {2} different '
              'edges ({3}), so CAE split it. The writer refuses any model in which something touches a '
              'curve away from its ends precisely because a spline\\'s split point cannot be predicted '
              'from anything adapy holds -- so this is a crossing that refusal did not see, and the '
              'member\\'s sub-edge count is now unknown rather than merely different.'.format(
                  set_name, part_name, len(indices), sorted(indices)))
    index = indices[0]
    built = part.edges[index].getSize(printResults=False)
    error = abs(built - expected_length) / expected_length
    _RESULT['curve_length_error'][set_name] = error
    if error > CURVE_LENGTH_REL_TOL:
        _fail('member {0!r} of part {1!r} is curved: adapy sampled its arc length as {2!r} and CAE built '
              '{3!r}, a relative difference of {4!r} against a tolerance of {5!r}. A spline through the '
              'sample points should be within 3e-06 of them; this far out means the wire is not that '
              'curve -- the chord of the same arc would read about 10% short.'.format(
                  set_name, part_name, expected_length, built, error, CURVE_LENGTH_REL_TOL))
    _RESULT['edges_per_member'][set_name] = 1
    return part.edges[index:index + 1]
'''


#: Emitted only when at least one section carries an offset, for the same reason.
_SECTION_OFFSET_GUARD = '''
def _guard_section_offsets(model):
    """Every planned beam offset is on the section CAE holds, unrounded.

    This is deliberately a readback and not a measurement of where the mass ended up:
    probed on Abaqus 2025, ``part.getMassProperties()`` reports the SAME mass and the SAME
    centre of mass for offsets (0, 0), (0.3, 0), (0, -0.4) and (0, 0.4) -- CAE's own mass
    model ignores a section offset entirely, so no in-kernel geometric query can see one.
    What can see it is the solver, and that is where the sign of this projection was pinned:
    a cantilever under an eccentric axial load reproduced adapy's own *MPC BEAM route's tip
    displacement to every printed digit.

    Each section names the keyword its own kind takes, and the guard reads back exactly that
    one -- a shaped section's ``beamSectionOffset`` or a generalized section's ``centroid``.
    Reading the other would be reading an attribute CAE refuses to let this script write.
    """
    problems = []
    for name in sorted(SECTION_OFFSETS.keys()):
        planned, keyword = SECTION_OFFSETS[name]
        if name not in model.sections.keys():
            problems.append('section {0!r} carries an offset but does not exist'.format(name))
            continue
        built = getattr(model.sections[name], keyword)
        pair = tuple([float(v) for v in built])
        _RESULT['section_offsets'][name] = [list(pair), keyword]
        if abs(pair[0] - planned[0]) > 0.0 or abs(pair[1] - planned[1]) > 0.0:
            problems.append('section {0!r}: adapy projected the offset {1} onto its (n1, n2) axes and CAE '
                            'holds {2} in {3}'.format(name, tuple(planned), pair, keyword))
    if problems:
        _fail('a beam section offset is not what adapy computed -- ' + ' | '.join(problems)
              + '. An offset that does not arrive is an eccentricity silently dropped, which is the '
                'coordinate error this writer refused outright until it could express it.')
'''


_TAIL = """

def main():
    model = _model()
    # Before anything is built: CAE would replace a reused name rather than refuse it.
    _guard_no_name_collisions(model)
    build(model)
    # Topology first of the post-build guards, because it is the one that says
    # whether the thing CAE built is even the same structure adapy described.
    _guard_topology(model)
    _guard_every_edge_sectioned(model)
    _guard_bounding_box(model)
{0}    _RESULT['ok'] = True
    _write_result()
    try:
        mdb.saveAs(pathName=os.path.join(os.path.dirname(_result_path()), CAE_NAME))
        _RESULT['cae_file'] = CAE_NAME
    except Exception:
        # The model is built and verified. Not being able to drop a .cae next to the
        # script is worth reporting, but it is not a build failure.
        _RESULT['errors'].append('could not save the .cae file:\\n' + traceback.format_exc())
    _write_result()
    print('ADAPY-CAE BUILD OK: ' + json.dumps(_RESULT['guards'], sort_keys=True))
    sys.stdout.flush()


try:
    main()
except Exception:
    # A half-built model must not look like a clean run. _fail records the traceback in
    # the sidecar and then forces a non-zero process status.
    _fail('unhandled exception during the build:\\n' + traceback.format_exc())
"""


def _tail(plan: _Plan) -> str:
    """``main()``, with the offset guard's call present only when there is an offset to guard."""
    offsets = any(use.offset is not None for use in plan.sections)
    return _TAIL.format("    _guard_section_offsets(model)\n" if offsets else "")


def _expected_topology_source(plan: _Plan) -> list[str]:
    """The topology guard's data, as source: what CAE must have built, per part.

    Written out in full rather than recomputed in the emitted script on purpose. adapy
    knows every endpoint, so the arithmetic belongs on this side; recomputing it in the
    kernel from the kernel's own geometry would be asking the model whether it agrees with
    itself.
    """
    lines = [
        "# What CAE must end up holding, computed by adapy from Beam.axis_global() before",
        "# a wire was drawn: one sub-edge per member, plus one more for every OTHER member's",
        "# endpoint that lands strictly inside it (CAE imprints a landing member into the",
        "# through member -- measured, 1 edge becomes 3). See _guard_topology.",
        "#",
        "# JOINT_TOL is adapy's own Config().general_point_tol -- the distance at which its FEM",
        "# node container calls two points one node. CAE's merge tolerance is CAE_MERGE_TOL,",
        "# measured on Abaqus 2025 and absolute in model units at every part size probed",
        "# (0.004, 4 and 4000 units long). The gap between the two is deliberate: a joint",
        "# closer than CAE_MERGE_TOL is built by both, one further apart than JOINT_TOL is",
        "# built by neither, and one in between is a member adapy joins and CAE leaves loose --",
        "# which is a real disagreement between adapy's two Abaqus routes and fails the build.",
        "JOINT_TOL = {0}".format(_num(plan.joint_tol)),
        "CAE_MERGE_TOL = {0}".format(_num(CAE_MERGE_TOL)),
        "EXPECTED_TOPOLOGY = {",
    ]
    for part_plan in plan.parts:
        topology = part_plan.topology
        lines += [
            "    {0!r}: {{".format(part_plan.cae_part_name),
            "        'edges': {0},".format(topology.edges),
            "        'vertices': {0},".format(topology.vertices),
            "        'edges_per_member': {",
        ]
        lines += [
            "            {0!r}: {1},".format(name, topology.edges_per_member[name])
            for name in sorted(topology.edges_per_member)
        ]
        lines += ["        },", "    },"]
    lines += ["}", ""]
    return lines


def _section_offsets_source(plan: _Plan) -> list[str]:
    """The beam offsets and the curve tolerance, each written only when the model has one.

    A straight, offset-free model emits neither -- so what the script carries is a statement
    about *this* model rather than a list of everything the writer can do.
    """
    offsets = [use for use in plan.sections if use.offset is not None]
    curved = [m for part in plan.parts for m in part.members if m.is_curved]
    lines: list[str] = []
    if curved:
        lines += [
            "# Curved members are drawn as WireSpline through points on their exact curve, and the",
            "# built edge's arc length is checked against the length adapy computed. Measured on",
            "# Abaqus 2025, a spline through points spaced {0} rad apart reproduced a quarter".format(
                _num(MAX_TURN_RADIANS)
            ),
            "# circle's arc length to 2.6e-06 relative; the chord of the same arc is 9.9% short.",
            "CURVE_LENGTH_REL_TOL = {0}".format(_num(CURVE_LENGTH_REL_TOL)),
            "# curved members in this model: {0}".format(len(curved)),
            "",
        ]
    if not offsets:
        return lines
    lines += [
        "# Beam offsets, as '{section: ((along n1, along n2), the CAE keyword that carries it)}', in",
        "# each section's own axes. adapy's e1 is a GLOBAL vector, so each of these is a projection",
        "# onto n1 = the beam's yvec (the vector the INP writer emits) and n2 = t x n1.",
        "#",
        "# The keyword differs by section kind, and that is measured rather than tidy: a shaped profile",
        "# takes 'beamSectionOffset' (written '*Beam Section Offset'), while a generalized one --",
        "# integration=BEFORE_ANALYSIS, which is where a channel has to go -- REFUSES it with",
        "# 'TypeError: keyword error on beamSectionOffset' from the constructor and from setValues",
        "# alike, and takes 'centroid' (written '*Centroid') instead.",
        "#",
        "# The sign is not a reading of Abaqus' convention. A cantilever under an eccentric axial load",
        "# reproduced adapy's own *MPC BEAM route's tip displacement to every printed digit for e.n2,",
        "# with both keywords, and exactly sign-flipped it for -e.n2. getMassProperties() cannot see an",
        "# offset at all -- mass and centre of mass are identical with one and without (measured) -- so",
        "# the in-kernel guard reads the section back rather than weighing it.",
        "SECTION_OFFSETS = {",
    ]
    lines += [
        "    {0!r}: (({1}, {2}), {3!r}),".format(
            use.cae_section_name, _num(use.offset[0]), _num(use.offset[1]), _offset_keyword(use)
        )
        for use in offsets
    ]
    lines += ["}", ""]
    return lines


def _planned_names_source(plan: _Plan) -> list[str]:
    """Every name the script will create, for guard 7 to check against the live model."""
    planned = {
        "parts": [p.cae_part_name for p in plan.parts],
        "instances": [p.cae_instance_name for p in plan.parts],
        "materials": [row.cae_name for row in plan.materials],
        "profiles": sorted({use.cae_profile_name for use in plan.sections}),
        "sections": [use.cae_section_name for use in plan.sections],
    }
    lines = [
        "# Every name this script creates. CAE does not refuse a reused name -- it replaces the",
        "# object and invalidates handles to the old one -- so a second run in one session would",
        "# silently swap them all. Checked before anything is built; see _guard_no_name_collisions.",
        "#",
        "# Sets are absent deliberately: they live inside a part, and a part whose own name is",
        "# free is a part this script just created, so its sets cannot collide with anything.",
        "PLANNED_NAMES = {",
    ]
    for kind in sorted(planned):
        lines.append("    {0!r}: [{1}],".format(kind, ", ".join(repr(name) for name in planned[kind])))
    lines += ["}", ""]
    return lines


def render_script(plan: _Plan, result_name: str, cae_name: str, adapy_version: str) -> str:
    """The emitted CAE script, as text."""
    boxes = _bounding_boxes(plan)
    lines: list[str] = []
    lines += _header(plan, result_name, adapy_version)
    lines += _PREAMBLE.split("\n")

    description = "adapy concept model {0!r}; units {1}".format(plan.root_name, plan.units)
    lines += [
        "MODEL_NAME = {0!r}".format(plan.model_name),
        "MODEL_DESCRIPTION = {0!r}".format(description),
        "RESULT_NAME = {0!r}".format(result_name),
        "CAE_NAME = {0!r}".format(cae_name),
        "",
        "# Corners adapy computed from Beam.axis_global(), in the emitted units. The script",
        "# checks the geometry it built against them; see _guard_bounding_box.",
        "EXPECTED_BBOX = {",
    ]
    lines += ["    {0!r}: ({1}, {2}),".format(name, _pt(boxes[name][0]), _pt(boxes[name][1])) for name in sorted(boxes)]
    lines += [
        "}",
        "BBOX_TOL = {0}".format(_num(_bbox_tolerance(boxes))),
        "",
    ]
    lines += _expected_topology_source(plan)
    lines += _section_offsets_source(plan)
    lines += _planned_names_source(plan)
    lines += [
        "_RESULT = {",
        "    'schema': 'ada.cae_build_result/3',",
        "    'ok': False,",
        "    'model': MODEL_NAME,",
        "    'source_part': {0!r},".format(plan.root_name),
        "    'created': {",
        "        'parts': [],",
        "        'materials': [],",
        "        'profiles': [],",
        "        'sections': [],",
        "        'sets': [],",
        "        'instances': [],",
        "        'section_assignments': 0,",
        "        'orientations': 0,",
        "    },",
        "    'edges_per_member': {},",
        "    'curve_length_error': {},",
        "    'section_offsets': {},",
        "    'guards': {},",
        "    'errors': [],",
        "    'skipped': [",
    ]
    lines += [
        "        {0},".format(json.dumps(s.as_dict(), sort_keys=True))
        for s in sorted(plan.skipped, key=lambda s: (s.kind, s.name))
    ]
    lines += ["    ],", "}", ""]
    lines += _RUNTIME_HELPERS.split("\n")
    if any(member.is_curved for part_plan in plan.parts for member in part_plan.members):
        lines += _CURVED_MEMBER_HELPER.split("\n")
    if any(use.offset is not None for use in plan.sections):
        lines += _SECTION_OFFSET_GUARD.split("\n")
    lines += [
        "",
        "def build(model):",
        '    """Everything this model is, in the order CAE needs it."""',
        "",
        "    # --- materials",
    ]

    for row in plan.materials:
        lines += [
            "    model.Material(name={0!r})".format(row.cae_name),
            "    model.materials[{0!r}].Elastic(table=(({1}, {2}),))".format(
                row.cae_name, _num(row.young), _num(row.poisson)
            ),
            "    model.materials[{0!r}].Density(table=(({1},),))".format(row.cae_name, _num(row.density)),
            "    _RESULT['created']['materials'].append({0!r})".format(row.cae_name),
        ]

    lines += ["", "    # --- profiles, from the section->profile mapping shared with the INP writer"]
    emitted_profiles: set[str] = set()
    for use in plan.sections:
        if use.cae_profile_name in emitted_profiles:
            continue
        emitted_profiles.add(use.cae_profile_name)
        lines += [
            # cae_kwargs_source(), not a sorted rendering: the dict is already in the CAE
            # kernel's own positional order, and a symbol argument such as
            # uniformThickness=OFF has to be emitted bare rather than quoted.
            "    model.{0}(name={1!r}, {2})".format(
                use.spec.cae_class, use.cae_profile_name, use.spec.cae_kwargs_source()
            ),
            "    _RESULT['created']['profiles'].append({0!r})".format(use.cae_profile_name),
        ]

    lines += ["", "    # --- beam sections, one per (profile, material, offset) triple"]
    materials_by_name = {row.cae_name: row for row in plan.materials}
    for use in plan.sections:
        if use.offset is not None:
            lines += [
                "    # offset {0} -- (along n1, along n2) in this section's own axes, projected from".format(
                    use.offset
                ),
                "    # adapy's global e1. Abaqus writes it as '*Beam Section Offset'; the sign was pinned",
                "    # against adapy's own *MPC BEAM route by the solver, which reproduced its tip",
                "    # displacement to every printed digit. getMassProperties() cannot see an offset at all.",
            ]
        if use.spec.cae_class == _NON_LINEAR_PROFILE_CLASS:
            # Measured: a GeneralizedProfile with integration=DURING_ANALYSIS makes CAE
            # write "**ERROR -- Generalized Profile cannot be used with this section."
            # into the INP it exports -- a model that builds in the GUI and cannot be
            # solved. Such a section integrates BEFORE_ANALYSIS and carries its own
            # elastic constants, so E, G, the Poisson ratio and the density go on the
            # section rather than being read from the material.
            row = materials_by_name[use.cae_material_name]
            lines.append(
                "    model.BeamSection(name={0!r}, profile={1!r}, material={2!r}, "
                "integration=BEFORE_ANALYSIS, poissonRatio={3}, density={4}, "
                "table=(({5}, {6}),){7})".format(
                    use.cae_section_name,
                    use.cae_profile_name,
                    use.cae_material_name,
                    _num(row.poisson),
                    _num(row.density),
                    _num(row.young),
                    _num(row.shear),
                    _offset_argument(use),
                )
            )
        else:
            lines.append(
                "    model.BeamSection(name={0!r}, profile={1!r}, material={2!r}, "
                "integration=DURING_ANALYSIS{3})".format(
                    use.cae_section_name,
                    use.cae_profile_name,
                    use.cae_material_name,
                    _offset_argument(use),
                )
            )
        lines.append("    _RESULT['created']['sections'].append({0!r})".format(use.cae_section_name))

    # Every call below is emitted inline, one statement per member, the way CAE's own
    # journal would write it: a helper that took the coordinates as arguments would hide
    # the model's graph behind a single call site, and that graph — which section covers
    # which region, located how — is the thing verification has to be able to read.
    for part_index, part_plan in enumerate(plan.parts):
        part_var = "part_{0}".format(part_index)
        lines += [
            "",
            "    # --- part {0!r}, {1} member(s)".format(part_plan.part_name, len(part_plan.members)),
            "    {0} = model.Part(name={1!r}, dimensionality=THREE_D, type=DEFORMABLE_BODY)".format(
                part_var, part_plan.cae_part_name
            ),
            "    _RESULT['created']['parts'].append({0!r})".format(part_plan.cae_part_name),
            "    # Every wire first. Coincident endpoints merge, and a member landing mid-span",
            "    # imprints a split, so a member located before its neighbours exist would miss",
            "    # the sub-edges those neighbours are about to create.",
        ]
        for member_index, member in enumerate(part_plan.members):
            if not member.is_curved:
                lines.append(
                    "    {0}.WirePolyLine(points=(({1}, {2}),), mergeType=IMPRINT, meshable=ON)".format(
                        part_var, _pt(member.p1), _pt(member.p2)
                    )
                )
                continue
            points_var = "curve_{0}_{1}".format(part_index, member_index)
            lines += [
                "    # {0!r}: {1} points on its exact curve, spaced so consecutive chords turn by no".format(
                    member.beam_name, len(member.path)
                ),
                "    # more than {0} rad. Arc length {1} (Richardson-extrapolated, not the chord sum); the".format(
                    _num(MAX_TURN_RADIANS), _num(member.curve_length)
                ),
                "    # built edge is checked against it below: a spline is an interpolation, not the curve.",
                "    {0} = (".format(points_var),
            ]
            lines += ["        {0},".format(_pt(point)) for point in member.path]
            lines += [
                "    )",
                "    {0}.WireSpline(points={1}, mergeType=IMPRINT, meshable=ON, smoothClosedSpline=OFF)".format(
                    part_var, points_var
                ),
            ]
        for member_index, member in enumerate(part_plan.members):
            edges_var = "edges_{0}_{1}".format(part_index, member_index)
            region_var = "region_{0}_{1}".format(part_index, member_index)
            lines += ["", "    # {0!r}".format(member.beam_name)]
            if member.is_curved:
                lines.append(
                    "    {0} = _curved_member_edges({1!r}, {2!r}, {3}, curve_{4}_{5}, {6})".format(
                        edges_var,
                        part_plan.cae_part_name,
                        member.cae_set_name,
                        part_var,
                        part_index,
                        member_index,
                        _num(member.curve_length),
                    )
                )
            else:
                lines += [
                    "    {0} = {1}.edges.getByBoundingCylinder(center1={2}, center2={3}, radius={4})".format(
                        edges_var, part_var, _pt(member.cyl1), _pt(member.cyl2), _num(member.radius)
                    ),
                    "    _member_edges({0!r}, {1!r}, {2})".format(
                        part_plan.cae_part_name, member.cae_set_name, edges_var
                    ),
                ]
            lines += [
                "    {0} = {1}.Set(name={2!r}, edges={3})".format(region_var, part_var, member.cae_set_name, edges_var),
                "    {0}.SectionAssignment(region={1}, sectionName={2!r})".format(
                    part_var, region_var, member.cae_section_name
                ),
                "    {0}.assignBeamSectionOrientation(region={1}, method=N1_COSINES, n1={2})".format(
                    part_var, region_var, _pt(member.n1)
                ),
            ]

    lines += [
        "",
        "    # --- assembly: exactly one instance per part",
        "    assembly = model.rootAssembly",
        "    assembly.DatumCsysByDefault(CARTESIAN)",
    ]
    for part_index, part_plan in enumerate(plan.parts):
        lines += [
            "    assembly.Instance(name={0!r}, part=part_{1}, dependent=ON)".format(
                part_plan.cae_instance_name, part_index
            ),
            "    _RESULT['created']['instances'].append({0!r})".format(part_plan.cae_instance_name),
        ]

    lines += _tail(plan).split("\n")
    return "\n".join(lines)


def write_cae_script(
    root: Part,
    destination: str | pathlib.Path,
    *,
    model_name: str = "Model-1",
    unit_scale: float = 1.0,
) -> list[pathlib.Path]:
    """Write ``destination``: an Abaqus/CAE script that rebuilds ``root`` as wires.

    Returns the paths *this call* wrote — the script, plus a ``<stem>.name_map.json``
    sidecar when sanitisation changed any name. ``<stem>.cae_build_result.json`` is
    written by the script itself, when CAE runs it.

    ``unit_scale`` must be ``1.0``; see :func:`check_unit_scale`.
    """
    from ada import __version__ as adapy_version

    destination = pathlib.Path(destination)
    if destination.suffix.lower() != ".py":
        raise CaeWriteError(
            "the CAE writer emits a Python script, so {0!r} needs a .py suffix".format(str(destination))
        )

    plan = build_plan(root, model_name=model_name, unit_scale=unit_scale)

    stem = destination.stem
    result_name = "{0}.cae_build_result.json".format(stem)
    text = render_script(
        plan,
        result_name=result_name,
        cae_name="{0}.cae".format(stem),
        adapy_version=adapy_version,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    written = [destination]

    name_map_path = destination.parent / "{0}.name_map.json".format(stem)
    if dump_name_map(plan.registries, name_map_path):
        written.append(name_map_path)

    logger.info(
        "Abaqus/CAE script %r written: %d part(s), %d beam(s), %d untranslated object(s)",
        str(destination),
        len(plan.parts),
        sum(len(p.members) for p in plan.parts),
        len(plan.skipped),
    )
    if plan.skipped:
        logger.warning(
            "%d physical object(s) are absent from the CAE script: it translates beams only. They are "
            "listed in the script header and in %s.",
            len(plan.skipped),
            result_name,
        )
    return written


__all__ = [
    "CURVED_BEAM_TYPES",
    "CURVE_N1_MIN_SIN",
    "CYLINDER_OVERSHOOT_FRACTION",
    "CYLINDER_RADIUS_FRACTION",
    "ECCENTRICITY_TOL",
    "MIN_BEAM_LENGTH",
    "REFUSED_BEAM_TYPES",
    "CaeWriteError",
    "SkippedObject",
    "UnsupportedBeamError",
    "beam_endpoints",
    "beam_n1",
    "beam_section_offset",
    "build_plan",
    "check_beam_shape",
    "check_n1_holds_along_the_curve",
    "check_unit_scale",
    "render_script",
    "write_cae_script",
]
