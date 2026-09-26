"""Where adapy keeps a concept model's supports and loads, and what CAE can carry of them.

Measured on the model this writer was asked to close a cross-solver comparison with, rather
than assumed — the question "where does adapy carry a ``Bc`` and a ``Load`` for a *concept*
model" has two answers and they are in different places:

**Two disjoint stores, and only one of them is what a solver run uses.**

1. :attr:`ada.Part.concept_fem` — a :class:`ada.fem.concept.base.ConceptFEM` holding
   ``constraints`` (:class:`~ada.fem.concept.constraints.ConstraintConceptPoint`,
   ``…Curve``, ``…RigidLink``, each with per-DOF ``fixed``/``free``/``spring``/
   ``prescribed``/``dependent``/``super``) and ``loads`` (``LoadConceptCase`` holding
   ``LoadConceptPoint``/``Line``/``Surface``/``AccelerationField``). This is the genuinely
   *conceptual* store: positions, not nodes, so it needs no mesh. It is what the GeniE XML
   reader fills (``ada.cadit.gxml.read.read_bcs``) and what the GeniE writer reads back.
2. :attr:`ada.fem.FEM.bcs` and :attr:`ada.fem.FEM.steps` — ``ada.fem.Bc`` records over a
   ``FemSet`` of **mesh nodes**, and ``ada.fem.Load`` records inside a ``Step``. This is the
   store every solver writer in the repo actually reads: the Sesam writer's ``BNBCD``/
   ``BNLOAD``, the Abaqus INP writer's ``*Boundary``/``*Cload``.

They are not two views of one thing. ``ada.extension.fem_concepts_builder`` — the one place
that consumes both — takes masses and load scenarios from (1) and boundary conditions from
(2), and says why in its own docstring: "BCs live on the simulation extension because they're
a property of the FEM, not the CAD part."

**This module reads (2).** That is the store a solve is defined in, so it is the store a CAE
model that is meant to be solved has to be built from; and (1) is refused rather than half
translated, loudly, by :func:`refuse_untranslated_concept_analysis`. A GeniE model whose
supports live only in ``concept_fem`` is a real case and it is named as such rather than
emitted without its supports.

Where the records are found is worth stating exactly, because it is not where one would
guess. For ``verification.genie_vs_abaqus.model.build_portal_frame`` — the model the
comparison is derived from — the two bases' ``Bc`` records are on ``part.fem.bcs``, while the
step carrying the two nodal forces is on the **assembly's** ``fem.steps``, not the part's.
So an analysis is assembly-wide even when its supports are not, and :func:`analysis_fems`
gathers from every FEM in the assembly (adapy's own ``FEM.get_all_bcs`` /
``FEM.get_all_steps`` idiom) rather than from the emitted part's subtree.

What is carried, and what is refused
====================================

Carried:

* a ``Bc`` of type ``displacement``, ``displacement/rotation`` or Abaqus'
  ``symmetry/antisymmetry/encastre``, as one ``DisplacementBC`` whose six keywords are the
  DOFs the record names, with ``UNSET`` for the ones it does not. **Including a non-zero
  magnitude**, written as a prescribed displacement. The Sesam writer defines
  ``PRESCRIBED = 2`` and never writes it -- its own comment says "ada's Bc magnitudes are
  not carried into BNDISPL yet" -- so a settlement case silently becomes a fixed support
  there. Abaqus expresses it natively and this writer writes it;
* a ``Load`` of type ``force``, as a ``ConcentratedForce`` named ``<load>_F`` for its three
  force components and a ``Moment`` named ``<load>_M`` for its three moment components --
  the same split, off the same ``Load.forces``, under the same two names as adapy's own INP
  writer's ``*Cload`` blocks, so the two Abaqus routes carry one load identically;
* a ``StepImplicitStatic``, as a ``StaticStep`` carrying its time and increment sizes, with
  ``previous`` chained through every step in order. adapy's Sesam writer emits **only the
  first** step of a multi-step deck and logs the rest away; that is not reproduced here.

Refused, each with the measurement or the reason in the message:

* every other ``Load`` type (``gravity``, ``acc``, ``acc_rot``, ``pressure``, ``mass``,
  ``force_set``). Abaqus has a keyword for several of them, and they are still refused rather
  than approximated: a ``Gravity`` load is only as good as the density it multiplies, and this
  writer substitutes ``1e-06`` for a material with no density (so that Abaqus accepts the
  deck at all), which would turn a gravity case into a model weighing nothing at all.
  The refusal is **at plan time with the type named**, which is the other half of why this is
  here: the Sesam writer's equivalent falls off the end of ``load_str`` returning ``None`` and
  surfaces as ``TypeError: can only concatenate str (not "NoneType") to str`` several frames
  from the cause, although ``ada.fem.exceptions`` has defined ``UnsupportedLoadType`` all
  along;
* a load or support carrying an ``Amplitude``, a ``Csys``, or an initial condition;
* a ``Bc`` of a velocity or connector type;
* a step that is not a static implicit one -- an eigenvalue, dynamic, explicit or
  steady-state step;
* steps on more than one FEM, because then nothing says what order they run in;
* a support that **prescribes a non-zero displacement and has no analysis step** to be applied
  in. Abaqus refuses a non-zero support in the initial step outright -- measured, as
  "Non-zero boundary condition in initial step." -- so a prescribed one is written into the
  first ``StaticStep`` and a fixed one into ``'Initial'``; with no step there is nowhere for it
  to go;
* a support or load whose nodes are **not at a vertex of the emitted geometry**. This is the
  refusal that keeps the analysis honest about what the writer builds: CAE carries a
  ``DisplacementBC`` on a geometric vertex, which survives re-meshing, and there is no vertex
  at a mesh node halfway along a member. Applying it to a located mesh node instead would
  work exactly once and silently move the next time the part was meshed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .names import NameRegistry
from .topology import distinct_points

if TYPE_CHECKING:
    from ada import Part
    from ada.fem import Bc, Load
    from ada.fem.steps import Step

#: ``Bc.type`` values this writer carries. All three become one ``DisplacementBC``: adapy
#: stores the DOFs explicitly in every case, so an Abaqus ``ENCASTRE``/``XSYMM`` record read
#: back into adapy carries the six-slot DOF list a ``DisplacementBC`` needs anyway, and
#: writing the DOFs rather than the shorthand keeps the prescribed magnitudes expressible.
CARRIED_BC_TYPES = (
    "displacement",
    "displacement/rotation",
    "symmetry/antisymmetry/encastre",
)

#: ``Bc.type`` values that are refused, and why. A velocity or a connector motion is not a
#: support: it is a different keyword with different physics, and a ``DisplacementBC`` written
#: from one would hold a *displacement* where the model said a rate.
REFUSED_BC_TYPES = {
    "velocity": (
        "a velocity boundary condition prescribes a rate, not a position. Abaqus writes one as "
        "'*Boundary, type=VELOCITY' and CAE as a VelocityBC, which is a different object with "
        "different physics -- so carrying it as a DisplacementBC would hold this model's rate as a "
        "displacement, at whatever numerical value it happened to be"
    ),
    "velocity/angular velocity": (
        "a velocity/angular-velocity boundary condition prescribes a rate, not a position; see the "
        "'velocity' refusal. It would be a VelocityBC in CAE, not a DisplacementBC"
    ),
    "connector_displacement": (
        "a connector motion acts on a connector element's own degrees of freedom, and this writer "
        "builds no connectors -- there is nothing in the emitted model for it to act on"
    ),
    "connector_velocity": (
        "a connector motion acts on a connector element's own degrees of freedom, and this writer "
        "builds no connectors -- there is nothing in the emitted model for it to act on"
    ),
}

#: The one ``Load.type`` this writer carries.
CARRIED_LOAD_TYPES = ("force",)

#: Every other ``Load.type``, and why each is refused rather than approximated.
REFUSED_LOAD_TYPES = {
    "gravity": (
        "Abaqus does have a Gravity load, and it is still refused: a gravity case is exactly as good "
        "as the density it multiplies, and this writer substitutes a density of 1e-06 for a material "
        "that has none (so that Abaqus accepts the deck at all, matching what adapy's INP writer "
        "does). A gravity load over such a material would weigh essentially nothing and solve without "
        "complaint. Apply the self weight in the CAE model once the densities are known to be right"
    ),
    "acc": (
        "an acceleration field is a '*Dload, GRAV' in Abaqus and carries the same dependence on every "
        "material's density as gravity does; see the 'gravity' refusal"
    ),
    "acc_rot": (
        "a rotational acceleration field needs a rotation origin and axis as well as a magnitude, and "
        "Abaqus expresses it as a centrifugal/Coriolis load rather than as one vector -- there is no "
        "faithful one-call translation of it"
    ),
    "force_set": (
        "a 'force_set' load has no writer anywhere in adapy, so what it means is not established " "enough to translate"
    ),
    "mass": (
        "a mass load is a point mass, which is an element and not a load. This writer builds beams "
        "only, and a MassPoint in the source part is already reported as untranslated"
    ),
    "pressure": (
        "a pressure load acts on a surface, and this writer translates beams only -- the emitted model "
        "has no face for it to act on. (adapy's Sesam writer cannot write one either, but it fails "
        "with a TypeError several frames from the cause rather than saying so)"
    ),
}

#: Abaqus' six ``DisplacementBC`` keywords, in adapy's DOF order 1..6.
BC_KEYWORDS = ("u1", "u2", "u3", "ur1", "ur2", "ur3")

#: ``ConcentratedForce`` and ``Moment`` keywords, in adapy's DOF order.
FORCE_KEYWORDS = ("cf1", "cf2", "cf3")
MOMENT_KEYWORDS = ("cm1", "cm2", "cm3")

#: Beam element codes this writer will mesh with, and what each one is.
#:
#: Restricted on purpose rather than passed through: the element decides the *physics* of the
#: answer, and these three are the ones whose behaviour against a closed form has been
#: measured on this writer's own output. On the portal frame that closes the cross-solver
#: comparison (6 elements per column, 8 per girder, a 200x10 tube):
#:
#:     B31  0.0248710 m   linear Timoshenko, converging from above as 1/n**2
#:     B32  0.0247459 m   quadratic Timoshenko -- already at B31's converged limit
#:     B33  0.0245990 m   cubic Euler-Bernoulli, exact in bending, no shear, mesh-independent
#:
#: A code outside this set is refused rather than emitted, because "it is a beam element" is
#: not enough to know what it would answer.
CARRIED_ELEMENT_TYPES = ("B31", "B32", "B33")

#: Shell element codes this writer will mesh a plate face with. ``S4R`` is the one measured against a
#: closed form on this writer's own output; see :func:`check_shell_element_type`.
CARRIED_SHELL_ELEMENT_TYPES = ("S4R", "S4", "S8R")

#: Below this a ``Bc`` magnitude is "fixed", not a prescribed displacement. A tolerance and not
#: a rounding: the value emitted is always the magnitude adapy holds, unrounded; this only
#: decides whether the emitted script's comment calls the support prescribed.
MAGNITUDE_TOL = 1e-12


class AnalysisNotSupported(Exception):
    """A support, load or step this writer will not approximate."""


@dataclass(frozen=True)
class RegionPlan:
    """One assembly-level CAE ``Set`` of vertices, which is what a BC or a load acts on.

    Assembly level rather than part level because that is where an Abaqus load lives: the
    region has to name the *instance*'s vertices, not the part's.
    """

    cae_set_name: str
    cae_instance_name: str
    #: The vertex positions, in the emitted model's own units, sorted.
    points: tuple[tuple[float, float, float], ...]
    #: The adapy ``FemSet`` this came from, for the emitted script's comment.
    source_set_name: str


@dataclass(frozen=True)
class BcPlan:
    """One adapy ``Bc`` as one CAE ``DisplacementBC``."""

    bc_name: str
    cae_name: str
    region: str
    #: ``'Initial'`` for a FEM-level support, or the step it was declared inside.
    step: str
    #: Six entries in adapy's DOF order: a float to prescribe, ``None`` to leave free.
    values: tuple[float | None, ...]
    bc_type: str

    @property
    def is_prescribed(self) -> bool:
        """Whether any DOF is held at a non-zero value."""
        return any(v is not None and abs(v) > MAGNITUDE_TOL for v in self.values)


@dataclass(frozen=True)
class LoadPlan:
    """One adapy ``Load`` of type ``force`` as a ``ConcentratedForce`` and/or a ``Moment``."""

    load_name: str
    region: str
    step: str
    forces: tuple[float, float, float]
    moments: tuple[float, float, float]
    follower: bool
    #: How many vertices the region holds. Abaqus applies the full component to **each** node
    #: of a region, exactly as ``*Cload`` over a node set does, so this multiplies the total.
    node_count: int

    @property
    def force_name(self) -> str:
        return "{0}_F".format(self.load_name)

    @property
    def moment_name(self) -> str:
        return "{0}_M".format(self.load_name)

    @property
    def has_force(self) -> bool:
        return any(component != 0.0 for component in self.forces)

    @property
    def has_moment(self) -> bool:
        return any(component != 0.0 for component in self.moments)


@dataclass(frozen=True)
class StepPlan:
    """One adapy ``StepImplicitStatic`` as a CAE ``StaticStep``."""

    step_name: str
    cae_name: str
    previous: str
    total_time: float
    init_incr: float
    min_incr: float
    max_incr: float
    total_incr: int
    nl_geom: bool


@dataclass
class AnalysisPlan:
    """Everything the emitted script needs in order to define and solve the analysis."""

    regions: list[RegionPlan] = field(default_factory=list)
    steps: list[StepPlan] = field(default_factory=list)
    bcs: list[BcPlan] = field(default_factory=list)
    loads: list[LoadPlan] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.steps or self.bcs or self.loads)

    def applied_resultant(self) -> tuple[float, float, float]:
        """The total applied force, summed over every load and every node it acts on.

        The emitted script checks the solver's own reaction sum against this. It is computed
        here, from adapy's records, so the check compares Abaqus against what the *model*
        said rather than against Abaqus' own bookkeeping.
        """
        total = [0.0, 0.0, 0.0]
        for load in self.loads:
            for axis in range(3):
                total[axis] += load.forces[axis] * load.node_count
        return (total[0], total[1], total[2])


def analysis_fems(root: Part) -> list[tuple[str, object]]:
    """``(part name, FEM)`` for every FEM whose supports and steps belong to this emission.

    Assembly-wide, and deliberately so: measured on
    ``verification.genie_vs_abaqus.model.build_portal_frame``, the supports are on the
    part's FEM while the step carrying the loads is on the **assembly's**. Gathering only
    the emitted part's subtree would therefore have emitted the frame's two fixed bases and
    silently dropped its 10 kN, which is the single failure this whole translation exists to
    prevent. It is also adapy's own idiom -- ``FEM.get_all_bcs`` and ``FEM.get_all_steps``
    both walk ``get_all_parts_in_assembly(include_self=True)``.

    The consequence is that writing *one part* of an assembly whose other parts carry
    supports will refuse rather than emit, because those supports have no vertex in the
    emitted geometry. That is the right outcome: half an analysis is not an analysis.
    """
    assembly = root.get_assembly()
    parts = assembly.get_all_parts_in_assembly(include_self=True)
    gathered: list[tuple[str, object]] = []
    for part in sorted(parts, key=lambda p: p.name):
        fem = getattr(part, "fem", None)
        if fem is None:
            continue
        if any(fem is seen for _, seen in gathered):
            continue
        gathered.append((part.name, fem))
    return gathered


def refuse_untranslated_concept_analysis(root: Part) -> None:
    """Refuse a model whose supports or loads live only in ``Part.concept_fem``.

    The *other* of adapy's two stores (see the module docstring). It is the one a GeniE model
    arrives in, and it is not translated here -- a ``ConstraintConceptPoint`` carries per-DOF
    ``spring``/``prescribed``/``dependent``/``super`` types with no Abaqus counterpart chosen
    yet, and a ``LoadConceptLine``/``Surface`` acts on geometry this writer does not build.

    Emitting the model without them would be the quietest possible way to lose a support, so
    the presence of any of them is a refusal that names them.
    """
    found: list[str] = []
    for part in sorted(root.get_all_subparts(include_self=True), key=lambda p: p.name):
        concept = getattr(part, "concept_fem", None)
        if concept is None:
            continue
        constraints = getattr(concept, "constraints", None)
        if constraints is not None:
            for kind in ("point_constraints", "curve_constraints", "rigid_links"):
                for name in sorted(getattr(constraints, kind, {}) or {}):
                    found.append("{0}.concept_fem.constraints.{1}[{2!r}]".format(part.name, kind, name))
        loads = getattr(concept, "loads", None)
        if loads is not None:
            for kind in ("load_cases", "load_case_combinations"):
                for name in sorted(getattr(loads, kind, {}) or {}):
                    found.append("{0}.concept_fem.loads.{1}[{2!r}]".format(part.name, kind, name))
        for name in sorted(getattr(concept, "steps", {}) or {}):
            found.append("{0}.concept_fem.steps[{1!r}]".format(part.name, name))
    if found:
        raise AnalysisNotSupported(
            "this model carries {0} analysis concept(s) in Part.concept_fem, which this writer does "
            "not translate: {1}. That is adapy's *other* store for supports and loads -- the one the "
            "GeniE XML reader fills, holding positions rather than mesh nodes -- and none of it is "
            "emitted below. A ConstraintConceptPoint's DOFs can be 'spring', 'prescribed', "
            "'dependent' or 'super', for which no Abaqus counterpart has been chosen and measured; a "
            "LoadConceptLine or LoadConceptSurface acts on geometry this writer does not build. So "
            "the model is refused rather than emitted without its supports and loads. Express the "
            "analysis as ada.fem.Bc records and a Step carrying ada.fem.Load records -- the store "
            "every solver writer in adapy reads -- or write the geometry from a part that carries no "
            "concept analysis.".format(len(found), ", ".join(found))
        )


def check_element_type(element_type: str) -> str:
    """The beam element code to mesh with, or a refusal naming the ones that are measured."""
    code = str(element_type).strip().upper()
    if code not in CARRIED_ELEMENT_TYPES:
        raise AnalysisNotSupported(
            "element_type={0!r} is refused. This writer meshes with {1} and nothing else: the element "
            "decides the physics of the answer, and those three are the ones whose behaviour has been "
            "measured against a closed form on this writer's own output (B31 linear Timoshenko, B32 "
            "quadratic Timoshenko, B33 cubic Euler-Bernoulli). 'It is a beam element' is not enough to "
            "know what a code would answer.".format(element_type, ", ".join(CARRIED_ELEMENT_TYPES))
        )
    return code


def check_shell_element_type(element_type: str) -> str:
    """The shell element code the plate faces are meshed with, or a refusal.

    Restricted for the reason :func:`check_element_type` restricts the beam codes: the element
    decides the physics of the answer. ``S4R`` is the one measured against a closed form on this
    writer's own output -- a 4 m x 0.5 m strip, 10 mm, at a 0.05 m seed under 1000 Pa gave
    ``max U3 = -0.173292979598045`` against ``5 q L**4 / (384 D) = 0.1733333...``, relative 2.3e-04.
    ``S4`` and ``S8R`` are admitted as the same element fully integrated and its quadratic
    counterpart; anything else is refused rather than passed through.
    """
    code = str(element_type).strip().upper()
    if code not in CARRIED_SHELL_ELEMENT_TYPES:
        raise AnalysisNotSupported(
            "shell_element_type={0!r} is refused. This writer meshes plate faces with {1} and nothing "
            "else. S4R is the code whose answer against a closed form has been measured on this "
            "writer's own output (a simply supported strip in cylindrical bending, 2.3e-04 relative); "
            "'it is a shell element' is not enough to know what another code would "
            "answer.".format(element_type, ", ".join(CARRIED_SHELL_ELEMENT_TYPES))
        )
    return code


def _node_positions(fem_set, owner: str) -> tuple[tuple[float, float, float], ...]:
    """The positions of a ``FemSet``'s nodes, refusing a set that is not nodes."""
    from ada import Node

    if fem_set is None:
        raise AnalysisNotSupported("{0} has no fem_set, so there is nothing for it to act on".format(owner))
    set_type = str(getattr(getattr(fem_set, "type", ""), "value", getattr(fem_set, "type", "")))
    members = list(getattr(fem_set, "members", []) or [])
    if not members:
        raise AnalysisNotSupported(
            "{0} acts on the empty set {1!r}. An empty region is not 'no support' or 'no load' -- it is "
            "a record that was meant to act somewhere and does not.".format(owner, fem_set.name)
        )
    positions = []
    for member in members:
        if not isinstance(member, Node):
            raise AnalysisNotSupported(
                "{0} acts on the set {1!r} (type {2!r}), whose member {3!r} is a {4} and not a Node. A "
                "DisplacementBC and a ConcentratedForce act on points; an element set has none.".format(
                    owner, fem_set.name, set_type, getattr(member, "name", member), type(member).__name__
                )
            )
        positions.append(tuple(float(c) for c in member.p))
    return tuple(sorted(positions))


def _resolve_region(
    positions: tuple[tuple[float, float, float], ...],
    vertices: list[tuple[tuple[float, float, float], str]],
    tol: float,
    owner: str,
    set_name: str,
) -> str:
    """Which emitted instance holds a vertex at every one of ``positions``.

    Matched by *position* rather than through the ``FemSet``'s parentage, on purpose: a node's
    position is the one thing that can be compared against the geometry this writer actually
    emits, so a part placement that the mesh and the wires disagree about is caught here
    rather than producing a support at the wrong place.
    """
    instances: set[str] = set()
    for point in positions:
        hits = [name for vertex, name in vertices if _distance(vertex, point) <= tol]
        if not hits:
            nearest = min(vertices, key=lambda item: _distance(item[0], point), default=None)
            nearest_text = (
                "the nearest vertex is at {0} in part instance {1!r}, {2:.6g} length units away".format(
                    tuple(round(c, 9) for c in nearest[0]), nearest[1], _distance(nearest[0], point)
                )
                if nearest is not None
                else "the emitted model has no vertices at all"
            )
            raise AnalysisNotSupported(
                "{0} acts at {1} through the set {2!r}, and the emitted geometry has no vertex there -- "
                "{3}. CAE carries a support or a load on a geometric vertex, which is what survives "
                "re-meshing; there is no vertex partway along a member, and locating a mesh node "
                "instead would work once and then move silently the next time the part was meshed. "
                "Split the member at that point in the source model so it becomes a joint, or move the "
                "record to a member end.".format(owner, tuple(round(c, 9) for c in point), set_name, nearest_text)
            )
        if len(set(hits)) > 1:
            raise AnalysisNotSupported(
                "{0} acts at {1} through the set {2!r}, and more than one emitted part has a vertex "
                "there: {3}. Which instance CAE should attach it to is then ambiguous, and picking one "
                "would apply the record to half the structure that meets at that "
                "point.".format(owner, tuple(round(c, 9) for c in point), set_name, sorted(set(hits)))
            )
        instances.add(hits[0])
    if len(instances) > 1:
        raise AnalysisNotSupported(
            "{0} acts through the set {1!r}, whose nodes are spread over more than one emitted part "
            "instance: {2}. One CAE region names one instance's vertices, so this record cannot be "
            "written as one object; split it per part in the source model.".format(owner, set_name, sorted(instances))
        )
    return instances.pop()


def _distance(a, b) -> float:
    return (((a[0] - b[0]) ** 2) + ((a[1] - b[1]) ** 2) + ((a[2] - b[2]) ** 2)) ** 0.5


def _bc_values(bc: Bc, owner: str) -> tuple[float | None, ...]:
    """The six ``DisplacementBC`` values, ``None`` where the record leaves a DOF free.

    ``zip(bc.dofs, bc.magnitudes)`` is how adapy's own Abaqus INP writer reads a ``Bc``, so
    the pairing is the same on both routes. What differs is that the magnitude is *kept*: see
    the module docstring on the Sesam writer's unwritten ``PRESCRIBED``.
    """
    dofs = list(bc.dofs or [])
    magnitudes = list(bc.magnitudes or [])
    values: list[float | None] = [None] * 6
    for index, dof in enumerate(dofs):
        if dof is None:
            continue
        try:
            number = int(dof)
        except (TypeError, ValueError) as exc:
            raise AnalysisNotSupported(
                "{0} names the degree of freedom {1!r}, which is not one of 1-6. A connector DOF or a "
                "symbolic name has no place in a DisplacementBC on a beam vertex.".format(owner, dof)
            ) from exc
        if number < 1 or number > 6:
            raise AnalysisNotSupported(
                "{0} names the degree of freedom {1}, and a beam node has six: 1-3 translations, 4-6 "
                "rotations.".format(owner, number)
            )
        magnitude = magnitudes[index] if index < len(magnitudes) else None
        value = 0.0 if magnitude is None else float(magnitude)
        previous = values[number - 1]
        if previous is not None and previous != value:
            raise AnalysisNotSupported(
                "{0} names the degree of freedom {1} twice, once at {2!r} and once at {3!r}. CAE takes "
                "one value per DOF, and choosing between them would be choosing what the model "
                "meant.".format(owner, number, previous, value)
            )
        values[number - 1] = value
    if all(value is None for value in values):
        raise AnalysisNotSupported(
            "{0} restrains no degree of freedom at all ({1!r}), so it is a support that supports "
            "nothing. An empty DisplacementBC in CAE is not the same thing as no support: it is an "
            "object a reader would take for one.".format(owner, bc.dofs)
        )
    return tuple(values)


def _load_components(load: Load, owner: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """A ``force`` load's three forces and three moments, off ``Load.forces``.

    ``Load.forces`` -- the DOF entry times the magnitude, with ``None`` read as zero -- is what
    adapy's Sesam, Code Aster and Abaqus INP writers all read, so using it is what keeps the
    four routes carrying one number.
    """
    if load.csys is not None:
        raise AnalysisNotSupported(
            "{0} is stated in a local coordinate system. Its components would have to be rotated into "
            "global axes first, and ``Load.forces_global`` logs an error and returns None when the "
            "csys has no coords -- which would arrive here as a load of nothing at all. Express the "
            "load in global axes.".format(owner)
        )
    if load.amplitude is not None:
        raise AnalysisNotSupported(
            "{0} carries the amplitude {1!r}. This writer emits no Amplitude objects, so the load "
            "would be written at its full magnitude for the whole step -- the same load, on a "
            "different history.".format(owner, getattr(load.amplitude, "name", load.amplitude))
        )
    components = load.forces
    if components is None or len(components) != 6:
        raise AnalysisNotSupported(
            "{0} has {1} component(s) rather than six, so which degrees of freedom it acts on cannot "
            "be read.".format(owner, "no" if components is None else len(components))
        )
    forces = tuple(float(c) for c in components[:3])
    moments = tuple(float(c) for c in components[3:])
    if not any(forces) and not any(moments):
        raise AnalysisNotSupported(
            "{0} has every component zero (magnitude {1!r}, dof {2!r}), so it is a load that loads "
            "nothing. CAE refuses one too -- 'AbaqusException: Load must be created with a non-zero "
            "magnitude unless utilizing a user subroutine.', measured on Abaqus 2025 -- so this is "
            "the same refusal, made earlier and with the adapy Load named.".format(owner, load.magnitude, load.dof)
        )
    return forces, moments


def _step_plan(step: Step, previous: str, cae_name: str) -> StepPlan:
    from ada.fem.steps import Step as StepBase
    from ada.fem.steps import StepImplicitStatic

    type_name = type(step).__name__
    if type(step) is not StepImplicitStatic or step.type != StepBase.TYPES.STATIC:
        raise AnalysisNotSupported(
            "step {0!r} is a {1} of type {2!r}, and this writer emits a CAE StaticStep only. An "
            "eigenvalue, dynamic, explicit or steady-state step is a different analysis with its own "
            "keywords and its own output, and writing it as a static step would answer a different "
            "question from the one the model asks.".format(step.name, type_name, step.type)
        )
    if step.interactions:
        raise AnalysisNotSupported(
            "step {0!r} carries {1} interaction(s) ({2}), and this writer emits no contact at all. A "
            "step whose contact is missing is a model that solves and is wrong.".format(
                step.name, len(step.interactions), ", ".join(sorted(step.interactions))
            )
        )
    if step.load_cases:
        raise AnalysisNotSupported(
            "step {0!r} carries {1} LoadCase object(s) ({2}), which this writer does not translate -- a "
            "Sesam load-case/combination structure has no one-to-one CAE counterpart. Express the loads "
            "as ada.fem.Load records on the step.".format(
                step.name, len(step.load_cases), ", ".join(sorted(step.load_cases))
            )
        )
    return StepPlan(
        step_name=step.name,
        cae_name=cae_name,
        previous=previous,
        total_time=float(step.total_time),
        init_incr=float(step.init_incr),
        min_incr=float(step.min_incr),
        max_incr=float(step.max_incr),
        total_incr=int(step.total_incr),
        nl_geom=bool(step.nl_geom),
    )


def plan_analysis(
    root: Part,
    vertices: list[tuple[tuple[float, float, float], str]],
    part_set_names: NameRegistry,
    tol: float,
) -> tuple[AnalysisPlan, dict[str, NameRegistry]]:
    """Resolve every support, load and step before a line of the analysis is written.

    ``vertices`` is ``[(position, cae instance name), ...]`` for every vertex the emitted
    geometry will hold -- member ends plus the points where one member imprints another, which
    is what :func:`ada.cadit.cae.topology.expected_topology` already computes. ``tol`` is
    adapy's own ``Config().general_point_tol``, the same tolerance the topology is stated at,
    so a support lands on a vertex exactly when adapy would call the two points one point.
    """
    refuse_untranslated_concept_analysis(root)

    registries = {
        "assembly sets": NameRegistry("assembly sets"),
        "boundary conditions": NameRegistry("boundary conditions"),
        "loads": NameRegistry("loads"),
        "steps": NameRegistry("steps"),
    }
    plan = AnalysisPlan()
    fems = analysis_fems(root)

    regions: dict[tuple, RegionPlan] = {}

    def region_for(fem_set, owner: str) -> RegionPlan:
        positions = _node_positions(fem_set, owner)
        key = (fem_set.name, positions)
        existing = regions.get(key)
        if existing is not None:
            return existing
        instance = _resolve_region(positions, vertices, tol, owner, fem_set.name)
        cae_set_name = registries["assembly sets"].allocate_shared(fem_set.name)
        if cae_set_name in part_set_names.taken:
            raise AnalysisNotSupported(
                "the support/load region {0!r} would become an assembly-level CAE set of that name, and "
                "a member of this model already claims it for a part-level set. CAE scopes the two "
                "repositories separately, so it would accept both -- and then the emitted script's "
                "result sidecar, which keys everything by set name, would hold one of them over the "
                "other, and a reader tracing the name back to a GeniE object would have no way to tell "
                "which. Rename one of them.".format(cae_set_name)
            )
        plan_region = RegionPlan(
            cae_set_name=cae_set_name,
            cae_instance_name=instance,
            points=positions,
            source_set_name=fem_set.name,
        )
        regions[key] = plan_region
        return plan_region

    def add_bc(bc: Bc, step_name: str | None, where: str) -> None:
        """``step_name=None`` means a FEM-level support: the step is chosen by what it holds."""
        owner = "boundary condition {0!r} ({1})".format(bc.name, where)
        if bc.type in REFUSED_BC_TYPES:
            raise AnalysisNotSupported(
                "{0} is of type {1!r}, which is refused: {2}.".format(owner, bc.type, REFUSED_BC_TYPES[bc.type])
            )
        if bc.type not in CARRIED_BC_TYPES:
            raise AnalysisNotSupported(
                "{0} is of type {1!r}, which this writer has never seen. It carries {2} and will not "
                "guess at anything else.".format(owner, bc.type, ", ".join(repr(t) for t in CARRIED_BC_TYPES))
            )
        if bc.amplitude is not None:
            raise AnalysisNotSupported(
                "{0} carries the amplitude {1!r}. This writer emits no Amplitude objects, so the "
                "support would be written at its full magnitude for the whole step -- the same "
                "support, on a different history.".format(owner, getattr(bc.amplitude, "name", bc.amplitude))
            )
        if getattr(bc, "_init_condition", None) is not None:
            raise AnalysisNotSupported(
                "{0} carries an initial condition, which this writer does not emit. A model that starts "
                "somewhere other than where it says it does is exactly the silent difference this "
                "writer refuses.".format(owner)
            )
        region = region_for(bc.fem_set, owner)
        values = _bc_values(bc, owner)
        prescribed = any(value is not None and abs(value) > MAGNITUDE_TOL for value in values)
        if step_name is None:
            # Measured, and not a preference: Abaqus refuses a non-zero support outright in the
            # initial step -- "Non-zero boundary condition in initial step." -- so a prescribed
            # displacement declared on a FEM is applied in the first analysis step, and only a
            # fully fixed one goes in 'Initial'. Getting this the other way round produced a
            # model that failed in the kernel rather than a wrong answer, which is how it was
            # found; recording it here is what stops it being rediscovered.
            if not prescribed:
                step_name = "Initial"
            elif first_step is None:
                raise AnalysisNotSupported(
                    "{0} prescribes a non-zero displacement {1} and this model has no analysis step "
                    "for it to be applied in. Abaqus refuses a non-zero boundary condition in the "
                    "initial step -- measured: 'Non-zero boundary condition in initial step.' -- so a "
                    "prescribed support needs a step. Add a StepImplicitStatic, or fix the support at "
                    "zero if that is what was meant.".format(
                        owner, tuple(value for value in values if value is not None)
                    )
                )
            else:
                step_name = first_step
        plan.bcs.append(
            BcPlan(
                bc_name=bc.name,
                cae_name=registries["boundary conditions"].allocate_unique(bc.name),
                region=region.cae_set_name,
                step=step_name,
                values=values,
                bc_type=bc.type,
            )
        )

    def add_load(load: Load, step_name: str) -> None:
        owner = "load {0!r} (step {1!r})".format(load.name, step_name)
        if load.type in REFUSED_LOAD_TYPES:
            raise AnalysisNotSupported(
                "{0} is of type {1!r}, which is refused: {2}.".format(owner, load.type, REFUSED_LOAD_TYPES[load.type])
            )
        if load.type not in CARRIED_LOAD_TYPES:
            raise AnalysisNotSupported(
                "{0} is of type {1!r}, which this writer has never seen. It carries {2} and will not "
                "guess at anything else.".format(owner, load.type, ", ".join(repr(t) for t in CARRIED_LOAD_TYPES))
            )
        region = region_for(load.fem_set, owner)
        forces, moments = _load_components(load, owner)
        registries["loads"].allocate_unique(load.name)
        plan.loads.append(
            LoadPlan(
                load_name=load.name,
                region=region.cae_set_name,
                step=step_name,
                forces=forces,
                moments=moments,
                follower=bool(load.follower_force),
                node_count=len(region.points),
            )
        )

    with_steps = [(part_name, fem) for part_name, fem in fems if list(fem.steps)]
    if len(with_steps) > 1:
        raise AnalysisNotSupported(
            "analysis steps are declared on {0} different FEMs ({1}), and nothing says what order they "
            "run in -- a CAE step chain is a total order, and each step's 'previous' has to name "
            "exactly one predecessor. Declare the steps on one FEM.".format(
                len(with_steps), ", ".join(part_name for part_name, _ in with_steps)
            )
        )

    # Steps before supports, because a FEM-level support that prescribes a displacement has to
    # name one -- see add_bc on why it cannot go in 'Initial'.
    previous = "Initial"
    step_records: list[tuple[Step, StepPlan]] = []
    for _, fem in with_steps:
        for step in fem.steps:
            cae_name = registries["steps"].allocate_unique(step.name)
            step_plan = _step_plan(step, previous, cae_name)
            plan.steps.append(step_plan)
            step_records.append((step, step_plan))
            previous = cae_name
    first_step = plan.steps[0].cae_name if plan.steps else None

    for part_name, fem in fems:
        for bc in sorted(fem.bcs, key=lambda b: b.name):
            add_bc(bc, None, "on {0}'s FEM".format(part_name))

    for step, step_plan in step_records:
        for bc in sorted(step.bcs.values(), key=lambda b: b.name):
            add_bc(bc, step_plan.cae_name, "declared inside step {0!r}".format(step.name))
        for load in step.loads:
            add_load(load, step_plan.cae_name)

    if plan.loads and not plan.steps:
        raise AnalysisNotSupported(
            "this model carries {0} load(s) and no analysis step, which cannot happen through adapy's "
            "own API -- a Load is added to a Step. Something has detached them, and a load with no "
            "step to apply it in would be written nowhere.".format(len(plan.loads))
        )

    plan.regions = [regions[key] for key in sorted(regions, key=lambda k: regions[k].cae_set_name)]
    return plan, registries


def vertex_index(part_plans, tol: float) -> list[tuple[tuple[float, float, float], str]]:
    """``[(vertex position, cae instance name), ...]`` for the geometry a plan will build.

    The same arithmetic the topology guard is stated with: a member's two ends, plus every
    point where another member's end lands strictly inside it, deduplicated at ``tol``.

    A part carrying plates contributes its ACIS body's own vertices as well, which are the plate
    corners. Those are places the emitted geometry genuinely has a vertex, so a support or a load
    sitting on one is expressible -- before plates existed there was nothing there to find, and
    such a record would have been refused for landing nowhere.
    """
    index: list[tuple[tuple[float, float, float], str]] = []
    for part_plan in part_plans:
        points: list[tuple[float, float, float]] = []
        for member in part_plan.members:
            points.append(tuple(float(c) for c in member.p1))
            points.append(tuple(float(c) for c in member.p2))
        points += [tuple(float(c) for c in point) for point in getattr(part_plan, "plate_vertices", ())]
        topology = part_plan.topology
        if topology is not None:
            for name in sorted(topology.splits):
                points += [tuple(float(c) for c in point) for point in topology.splits[name]]
        for point in distinct_points(points, tol):
            index.append((point, part_plan.cae_instance_name))
    return index


__all__ = [
    "BC_KEYWORDS",
    "CARRIED_BC_TYPES",
    "CARRIED_ELEMENT_TYPES",
    "CARRIED_SHELL_ELEMENT_TYPES",
    "CARRIED_LOAD_TYPES",
    "FORCE_KEYWORDS",
    "MAGNITUDE_TOL",
    "MOMENT_KEYWORDS",
    "REFUSED_BC_TYPES",
    "REFUSED_LOAD_TYPES",
    "AnalysisNotSupported",
    "AnalysisPlan",
    "BcPlan",
    "LoadPlan",
    "RegionPlan",
    "StepPlan",
    "analysis_fems",
    "check_element_type",
    "check_shell_element_type",
    "plan_analysis",
    "refuse_untranslated_concept_analysis",
    "vertex_index",
]
