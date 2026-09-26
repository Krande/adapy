"""The plate concept model: a simply supported strip, bare and with one stiffener.

Written once, here, and both solver inputs derived from it -- the same premise as
:mod:`model`, applied to shells. What is different, and what drove every choice below, is
that **shells do not agree node-for-node the way beams did**. Sestra meshes the Sesam deck
with ``FQUS`` and Abaqus meshes the CAE geometry with ``S4R``; the two elements are
different formulations and both converge with mesh, so the comparison is a
*mesh-convergence bracket* (:mod:`plate_compare`) rather than a single-mesh table.

The structure
=============

A 4.0 x 0.5 m strip, 10 mm, S355, simply supported on the two **short** edges and held in
**cylindrical bending** by ``u2 = 0, ur1 = 0`` on both long edges, under 1000 Pa. In two
variants: bare, and with a 10 x 45 mm flat bar along the centreline (a ``Stringer`` in CAE,
a ``BEAS`` sharing the shell's nodes in Sesam).

The dimensions are not free choices; each one buys a term in the closed form
(:mod:`plate_hand_check`):

**The long-edge constraint is what makes the closed form the right one.** A strip in
cylindrical bending has stiffness ``D = E t^3 / (12 (1 - nu^2))`` per unit width and
deflects ``5 q L^4 / (384 D)``. A strip with *free* long edges curves anticlastically and
sits somewhere between that and ``E I`` with no ``1 - nu^2``, which is 9% away -- and which
regime it is in is set by ``b^2 / (R t)``, about 2.9 here, i.e. squarely in the ambiguous
middle. Constraining the long edges removes the ambiguity rather than budgeting for it. The
strip's transverse uniformity is then *checked*, not assumed: see
:func:`plate_compare.assert_cylindrical`, and measured, both solvers put the same
displacement at ``y = 0``, ``y = b/2`` and ``y = b`` to ten figures in the bare case.

**The bar's centroid is on the plate's mid-surface**, because CAE assigns the shell section
at ``MIDDLE_SURFACE`` and puts the stringer on the edge of that surface, and adapy's Sesam
writer likewise gives the beam no eccentricity. So the plate and the bar bend about the
*same* axis and their stiffnesses simply add: the parallel-spring estimate is exact rather
than indicative, with no ``E A e^2`` term to get wrong. That is what makes the stiffened
variant a second independent closed form instead of a second unknown.

**The bar's 45 mm dimension stands out of the plate.** Both writers arrive at that from the
model without being told: the CAE writer emits ``n1=(0, 1, 0)`` for this member and adapy's
section properties give ``Iy = 7.59375e-08 = a b^3 / 12``, which is the axis the strip bends
about. A bar rotated 90 degrees would have ``I = 3.75e-09`` -- 20x smaller -- and would
leave the stiffened answer indistinguishable from the bare one, which is why
:func:`plate_compare.assert_stiffener_present` measures the stiffness *ratio* rather than
trusting that a beam appeared in the deck.

Why three mesh sizes, and these three
=====================================

:data:`MESH_SIZES` is ``(0.125, 0.0625, 0.03125)`` -- a factor of two each time, which is
what lets an order be read off three values (:func:`plate_hand_check.observed_order`) and a
Richardson extrapolation be taken without assuming one. Measured, both solvers converge at
second order on the bare strip -- the single error ratio three meshes give is 4.0000 for Sestra
and 3.9997 for Abaqus -- and the extrapolants agree to 1.720e-05; see :mod:`plate_compare` for
the whole table.

Each size divides both 4.0 and 0.5 into an even count, and adapy's mesher and CAE's
``seedPart`` land on the **same structured grid** -- 33 x 5, 65 x 9, 129 x 17 nodes, 165 /
585 / 2193, identical on both sides at every density, measured. That is a convenience, not
the mechanism: correspondence is still established by position
(:mod:`displacements`), and the node ids the two sides use are different at every probe.

What each side can and cannot be given
======================================

Two writer gaps decide how the load and the supports reach each solver. Both are reported
rather than worked around silently, and both are stated in the runners' docstrings:

1. **adapy's Sesam writer has no distributed-load record at all.** ``write_loads.load_str``
   reports ``[OMITTED] a "pressure" load is not written by the Sesam writer`` and returns
   ``""``; there is no ``BEUSLO`` or ``BELOAD`` anywhere in ``ada/fem/formats/sesam/write``.
   So a pressure reaches Sestra as *nothing*, and a Sestra run of this model would solve an
   unloaded strip -- the exact "0 vs 0, agree" failure :mod:`compare` exists to prevent. The
   Sestra side therefore carries :func:`consistent_nodal_loads` instead, which is not an
   approximation: for a 4-node bilinear quad ``integral(N_i) dA = A / 4`` exactly, so the
   consistent load vector of a uniform pressure *is* ``q A / 4`` at each of an element's
   four nodes. Summed, that is 2000.000000 N against ``q L b = 2000`` (measured), and both
   solvers' own reaction totals are checked against it.
2. **The CAE writer carries a support only on a geometric vertex.**
   ``ada.cadit.cae.analysis._resolve_region`` resolves a ``FemSet``'s node positions against
   the vertices of the emitted geometry, and the interior nodes of a plate edge are not
   vertices -- so a ``Bc`` on this strip's supported edge is refused by name (reproduced in
   :mod:`plate_abaqus_runner`). The Abaqus side therefore gets those three supports from a
   driver appended to the emitted script, generated **from these very** :class:`ada.fem.Bc`
   **records** and through ``analysis.BC_KEYWORDS``, so the two decks cannot drift apart.

So the model is built with the load in one of two equivalent forms, chosen by the solver
that will read it (:data:`LOAD_STYLES`), and everything else -- geometry, thickness,
material, section, stiffener, supports, step -- is the one object both sides derive from.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

import numpy as np

import ada
from ada.fem import Bc, FemSet, Load, StepImplicitStatic
from ada.materials.metals import CarbonSteel

from .model import ProbePoint

#: Strip span, metres, between the two simply supported edges.
STRIP_LENGTH = 4.0

#: Strip width, metres. The direction held in cylindrical bending.
STRIP_WIDTH = 0.5

#: Plate thickness, metres. ``L / t = 400``, so this is a thin plate and transverse shear
#: contributes at the 1e-05 level -- far below the discretisation the study measures.
PLATE_THICKNESS = 0.010

#: S355: E = 210 GPa, nu = 0.3 (adapy's ``CarbonSteel`` defaults).
MATERIAL_NAME = "S355"

#: Uniform pressure, pascals. 1000 Pa gives 0.173 m of deflection -- 4.3% of the span, which
#: is large for a physical plate but is what both solvers are asked to reproduce *linearly*,
#: and both are run geometrically linear. Kept at the value the CAE writer's own closed-form
#: acceptance test uses, so its measured 0.17329297959804535 at a 0.05 seed remains a
#: directly comparable fourth data point.
PRESSURE = 1000.0

#: The flat bar's dimensions, metres: 10 mm thick, 45 mm deep. ``w x h`` of an adapy ``FB``.
BAR_WIDTH = 0.010
BAR_HEIGHT = 0.045

#: Element seed sizes, metres, coarse to fine. A factor of two apart -- see the module
#: docstring. Every one divides both :data:`STRIP_LENGTH` and :data:`STRIP_WIDTH` into an
#: even count, which is what puts a node at every probe.
MESH_SIZES: tuple[float, ...] = (0.125, 0.0625, 0.03125)

#: Name of the single load case / step.
LOAD_CASE = "LC1"
STEP_NAME = "static"

#: Which writer the model is being built for, and therefore which of two forced choices it gets.
#:
#: There is **one** structure, one mesh, one thickness, one material, one stiffener and one
#: statement of "simply supported" (:data:`EDGE_SUPPORTS`). What the route decides is only what
#: each writer can be handed at all, and both halves of it are the writer gaps in the module
#: docstring rather than modelling preferences:
#:
#: =========  ==========================  ==========================================================
#: route      load                        supports
#: =========  ==========================  ==========================================================
#: ``sestra`` the exact consistent nodal  the three ``Bc`` records, which become ``BNBCD``
#:            vector -- the Sesam writer
#:            emits no distributed load
#: ``abaqus`` one ``Load`` of type        **none in the model**: the CAE writer resolves a support
#:            ``pressure``, which becomes  to a geometric vertex, so a ``Bc`` on a plate edge is
#:            a ``Surface`` + ``*Dsload``  refused by name. They are emitted by
#:                                         :func:`plate_abaqus_runner.support_driver`, generated
#:                                         from :data:`EDGE_SUPPORTS` and ``analysis.BC_KEYWORDS``
#: =========  ==========================  ==========================================================
#:
#: The refusal is reproducible on demand -- :func:`plate_abaqus_runner.reproduce_edge_support_refusal`
#: builds the ``abaqus`` route *with* the records and returns what the writer says about them -- so
#: it is a measured gap in this package rather than a remembered one, and the day the CAE writer
#: grows edge regions that function stops raising and says so.
ROUTES = ("sestra", "abaqus")

#: How the uniform load is expressed for each route. ``"nodal"`` is the exact consistent load
#: vector; ``"pressure"`` is the ``ada.fem.Load`` of type ``pressure``.
LOAD_STYLES = {"sestra": "nodal", "abaqus": "pressure"}

#: The supported and constrained edges, as ``(set name, adapy dof list, what it means)``.
#:
#: Written out rather than inlined because the Abaqus driver is generated from exactly this
#: through ``ada.cadit.cae.analysis.BC_KEYWORDS`` -- "simply supported" has to mean the same
#: thing in both decks, and the only way to be sure is for there to be one statement of it.
#:
#: ``x = 0`` takes ``u1`` as well, to stop the strip sliding along its own span: with ``u1``
#: free at both ends the in-plane mode is unrestrained. ``x = L`` leaves ``u1`` free, which
#: is what makes the support *simple* rather than a membrane restraint -- a strip stretched
#: between two fixed ends carries part of the load in tension and is 15% stiffer at this
#: deflection. **No rotation is fixed on either supported edge**: ``ur2`` (about the width
#: axis) is free at ``x = 0`` and ``x = L``, which is the whole content of "simply
#: supported", and the measured support rotation ``0.1386667`` = ``q L^3 / (24 D)`` is what
#: confirms it arrived (:func:`plate_hand_check.support_rotation`).
EDGE_SUPPORTS = (
    ("SS_X0", (1, 2, 3), "simply supported, and the span's in-plane restraint: u1 = u2 = u3 = 0, rotations free"),
    ("SS_X1", (2, 3), "simply supported, free to slide along the span: u2 = u3 = 0, rotations free"),
    ("CYL", (2, 4), "cylindrical bending on both long edges: u2 = 0, ur1 = 0"),
)

#: The points compared between solvers, all of them on the strip's own mesh lines.
#:
#: The three at mid-span are the cylindrical-bending check: in cylindrical bending
#: ``MID_Y0``, ``MID`` and ``MID_YB`` carry the *same* displacement, and measured they do to
#: ten figures on the bare strip in both solvers. If the long-edge constraint were lost they
#: would fan out, which is a defect a mid-span-only probe set could not see --
#: :func:`plate_compare.assert_cylindrical`.
#:
#: The two supported-edge points are zero in ``u3`` by construction and are included for the
#: same reason the portal frame includes its bases: they are how the comparison sees that the
#: supports survived translation. Their ``ur2`` is *not* zero -- it is the support rotation
#: the closed form predicts -- so these two probes carry real signal as well as the zero.
PROBE_POINTS: tuple[ProbePoint, ...] = (
    ProbePoint("X0_MID", (0.0, STRIP_WIDTH / 2, 0.0), "supported edge, centreline: u3 = 0, ur2 free"),
    ProbePoint("QTR", (STRIP_LENGTH / 4, STRIP_WIDTH / 2, 0.0), "quarter span, centreline"),
    ProbePoint("MID", (STRIP_LENGTH / 2, STRIP_WIDTH / 2, 0.0), "mid-span, centreline: the peak deflection"),
    ProbePoint("TQTR", (3 * STRIP_LENGTH / 4, STRIP_WIDTH / 2, 0.0), "three-quarter span, centreline"),
    ProbePoint("X1_MID", (STRIP_LENGTH, STRIP_WIDTH / 2, 0.0), "supported edge, centreline: u3 = 0, ur2 free"),
    ProbePoint("MID_Y0", (STRIP_LENGTH / 2, 0.0, 0.0), "mid-span on the y = 0 long edge"),
    ProbePoint("MID_YB", (STRIP_LENGTH / 2, STRIP_WIDTH, 0.0), "mid-span on the y = b long edge"),
)

#: The probe whose deflection the closed form predicts.
DEFLECTION_PROBE = "MID"

#: The probe whose ``ur2`` the closed-form support rotation predicts.
ROTATION_PROBE = "X0_MID"

#: The three mid-span probes, in width order. Their ``u3`` must agree for the strip to be in
#: cylindrical bending at all.
CYLINDRICAL_PROBES = ("MID_Y0", "MID", "MID_YB")

#: adapy's own name for the plate and the bar, and the part. Carried as constants because
#: both runners and the emitted CAE driver name them.
PART_NAME = "Strip"
PLATE_NAME = "strip"
BAR_NAME = "bar"


class PlateModelInvalid(ValueError):
    """The model or mesh handed in is not the shell model this comparison is about.

    Raised rather than worked around: a plate comparison run on a mesh with no shell
    elements in it would compare two numbers neither of which came from a plate, and the
    tables would look perfectly ordinary.
    """


@dataclass(frozen=True)
class NodalLoadGroup:
    """One tributary area and every node that carries exactly it.

    A group, not a node, because that is the shape both writers want: adapy's Sesam writer
    emits one ``BNLOAD`` per member of a ``Load``'s node set with the same vector on each, so
    a uniform grid needs one ``Load`` per *distinct* tributary area -- three of them for a
    structured quad mesh (corner, edge, interior), whatever the density.
    """

    #: Tributary area, square metres: the sum of ``A_element / 4`` over the elements touching
    #: these nodes.
    area: float
    #: The node ids, sorted.
    node_ids: tuple[int, ...]

    @property
    def force(self) -> float:
        """The ``u3`` force on each of these nodes, newtons. Negative: the pressure pushes
        against the plate's ``+z`` normal, which is the sign Abaqus' ``side1Faces`` gives a
        positive pressure magnitude."""
        return -PRESSURE * self.area


def section_properties() -> dict[str, float]:
    """The material and bar-section numbers the closed forms need, read off the model.

    Read rather than retyped, for the reason :func:`model.section_properties` gives: a
    changed :data:`BAR_HEIGHT` must not leave a stale ``I`` in the hand check. ``Iy`` is the
    second moment about the axis the strip bends about, and it is the one adapy writes into
    the Sesam ``GBEAMG`` record and the one the CAE writer's ``n1=(0, 1, 0)`` puts in the same
    place -- measured ``7.59375e-08``, which is ``BAR_WIDTH * BAR_HEIGHT**3 / 12`` exactly.
    """
    mat = ada.Material(MATERIAL_NAME, CarbonSteel(MATERIAL_NAME))
    props = bar_section().properties
    model_props = mat.model
    return {
        "E": float(model_props.E),
        "nu": float(model_props.v),
        "rho": float(model_props.rho),
        "bar_area": float(props.Ax),
        "bar_Iy": float(props.Iy),
        "bar_Iz": float(props.Iz),
    }


def bar_section() -> ada.Section:
    """The flat bar, as one object both the model and :func:`section_properties` use."""
    return ada.Section("BAR", "FB", h=BAR_HEIGHT, w_top=BAR_WIDTH, w_btn=BAR_WIDTH)


def build_strip(
    mesh_size: float, *, stiffened: bool, route: str = "sestra", with_edge_supports: bool | None = None
) -> ada.Assembly:
    """The one definition of the strip. Both solver inputs are derived from this.

    Returns an :class:`ada.Assembly` carrying:

    * a shell FEM on the ``Strip`` part at ``mesh_size``, quadrilateral
      (``use_quads=True``) -- ``FQUS`` in Sesam, ``S4R`` in Abaqus, and the closed form is
      for a plate, so a triangulated mesh would be comparing two different discretisations
      of two different element families;
    * when ``stiffened``, a ``Beam`` along the centreline meshed as line elements that share
      the shells' nodes. Checked, not hoped for: see :func:`assert_stiffener_shares_nodes`;
    * node sets named after :data:`EDGE_SUPPORTS`, always -- and a ``Bc`` on each of them on
      the ``sestra`` route only, because the CAE writer refuses one there (see :data:`ROUTES`);
    * one ``StepImplicitStatic`` carrying the load form the route can be given.

    ``route`` is the only parameter that changes what the two writers see, and both of its
    effects are writer gaps rather than modelling choices -- the table in :data:`ROUTES` is
    the whole of it. ``with_edge_supports`` overrides the support half of it, and exists for
    one caller: :func:`plate_abaqus_runner.reproduce_edge_support_refusal`, which asks the CAE
    writer for the thing it refuses so the refusal is measured rather than remembered.
    """
    if route not in ROUTES:
        raise PlateModelInvalid(f"route must be one of {ROUTES}, got {route!r}")
    load_style = LOAD_STYLES[route]
    if with_edge_supports is None:
        with_edge_supports = route == "sestra"

    mat = ada.Material(MATERIAL_NAME, CarbonSteel(MATERIAL_NAME))
    outline = [(0.0, 0.0), (STRIP_LENGTH, 0.0), (STRIP_LENGTH, STRIP_WIDTH), (0.0, STRIP_WIDTH)]
    objects: list = [ada.Plate(PLATE_NAME, outline, PLATE_THICKNESS, mat=mat)]
    if stiffened:
        objects.append(
            ada.Beam(BAR_NAME, (0.0, STRIP_WIDTH / 2, 0.0), (STRIP_LENGTH, STRIP_WIDTH / 2, 0.0), bar_section(), mat)
        )

    part = ada.Part(PART_NAME) / objects
    assembly = ada.Assembly("StripSite") / part
    # "line" is bm_repr: the plate is a shell (the default pl_repr) and the bar is a beam.
    # Meshing the bar as shells too would be a different structure, and a different closed
    # form -- the parallel-spring estimate is for a bar bending about the plate's own axis.
    part.fem = part.to_fem_obj(mesh_size, "line", use_quads=True, interactive=False)
    fem = part.fem

    assert_has_shells(fem, mesh_size=mesh_size, stiffened=stiffened)
    if stiffened:
        assert_stiffener_shares_nodes(fem)
    assert_probes_are_seeded(fem, mesh_size=mesh_size)

    for set_name, dofs, _why in EDGE_SUPPORTS:
        # The set is added either way, so that both decks carry the same named node groups and a
        # reader can see which nodes the driver's geometry edges have to cover.
        fem_set = fem.add_set(_edge_nset(fem, set_name))
        if with_edge_supports:
            fem.add_bc(Bc(set_name, fem_set, list(dofs)))

    step = assembly.fem.add_step(StepImplicitStatic(STEP_NAME, nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    if load_style == "nodal":
        _add_nodal_loads(fem, step)
    else:
        _add_pressure(fem, step)
    return assembly


def shell_elements(fem) -> list:
    """Every quadrilateral shell element of ``fem``, sorted by id.

    Sorted because the tributary areas are summed over them and a dict keyed on a float is
    then reproducible -- the determinism rule, applied where it actually bites.
    """
    return sorted((el for el in fem.elements if str(el.type).endswith("QUAD")), key=lambda el: el.id)


def assert_has_shells(fem, *, mesh_size: float | None = None, stiffened: bool | None = None) -> None:
    """Raise :class:`PlateModelInvalid` unless ``fem`` holds quadrilateral shell elements.

    The loudest of this module's guards and the cheapest. A plate comparison whose mesh is
    all beams, or all triangles, still produces two tables of six components at seven probes
    and a comparison report full of numbers; nothing downstream would notice. The tributary
    areas would also silently come out zero, which is the "0 vs 0, agree" failure in another
    costume.
    """
    shells = shell_elements(fem)
    if not shells:
        kinds = sorted({str(el.type) for el in fem.elements})
        raise PlateModelInvalid(
            f"this FEM carries no quadrilateral shell elements at all -- element types present: "
            f"{kinds or '(none)'}, {len(fem.elements)} element(s), mesh_size={mesh_size}, "
            f"stiffened={stiffened}. Every number this comparison produces comes from plate "
            f"bending, and a uniform pressure resolved onto no shells is a zero load vector. "
            f"Build the FEM with to_fem_obj(..., 'line', use_quads=True), which meshes a Plate "
            f"as shells and a Beam as line elements."
        )


def assert_stiffener_shares_nodes(fem) -> None:
    """Raise unless every node on the stiffener line is used by a shell *and* a beam element.

    This is the adapy-side half of the measurement the CAE writer's ``Stringer`` finding is
    about: a stiffener that is present in the deck and attached to nothing carries no load,
    and the deflection then equals the bare strip's. Measured on this model at every density:
    33 / 65 / 129 nodes on the line, all of them shared, and the mesh gains **no** nodes for
    the bar (165 / 585 / 2193 either way).

    The Abaqus side is checked in the emitted script's own guards and by
    :func:`plate_compare.assert_stiffener_present`, which measures the stiffness the bar
    actually contributes rather than counting elements.
    """
    on_line = [n for n in fem.nodes if abs(n.y - STRIP_WIDTH / 2) < 1e-9 and abs(n.z) < 1e-9]
    shared = 0
    for node in on_line:
        kinds = {str(ref.type) for ref in node.refs if hasattr(ref, "type")}
        if len(kinds) > 1:
            shared += 1
    if not on_line or shared != len(on_line):
        raise PlateModelInvalid(
            f"the stiffener is not attached to the plate in adapy's own mesh: of the {len(on_line)} "
            f"node(s) on y = {STRIP_WIDTH / 2}, {shared} are used by both a shell and a beam element. "
            f"A bar that has elements and shares no node carries no load, and the stiffened strip "
            f"would then deflect exactly like the bare one -- see "
            f"plate_compare.assert_stiffener_present for the measurement that catches it downstream."
        )


def assert_probes_are_seeded(fem, *, mesh_size: float | None = None) -> None:
    """Raise unless every :data:`PROBE_POINTS` coordinate is a unique node of ``fem``.

    Checked on the adapy mesh before a solve is spent on it, exactly as
    :func:`model.assert_probes_are_seeded` does for the frame. The Abaqus mesh is checked by
    :func:`displacements.sample_fea_result`, which raises rather than settling for the
    nearest node.
    """
    missing = []
    for probe in PROBE_POINTS:
        if len(fem.nodes.get_by_volume(p=probe.xyz, tol=1e-6)) != 1:
            missing.append(f"{probe.name} at {probe.xyz}")
    if missing:
        raise PlateModelInvalid(
            f"the mesh has no unique node at these probe points, so the comparison would be "
            f"sampling the wrong place: {missing}. mesh_size={mesh_size} must divide "
            f"STRIP_LENGTH={STRIP_LENGTH} and STRIP_WIDTH={STRIP_WIDTH} into an even number of "
            f"elements; MESH_SIZES={MESH_SIZES} all do."
        )


def consistent_nodal_loads(fem) -> tuple[NodalLoadGroup, ...]:
    """The nodal load vector of :data:`PRESSURE` on ``fem``'s shells, grouped by magnitude.

    **Not an approximation.** For a 4-node bilinear quadrilateral ``integral(N_i) dA = A / 4``
    for each of the four shape functions, so the work-equivalent (consistent) nodal load of a
    uniform pressure *is* ``q A / 4`` at each node of each element -- which summed over the
    elements touching a node is ``q`` times that node's tributary area. Both solvers mesh
    this strip with 4-node quads (``FQUS``, ``S4R``), so the vector below is the load Abaqus'
    own ``*Dsload`` builds internally, not a lumping of it. Measured: the areas sum to
    2.000000 m2 against ``L b = 2.0``, the forces to -2000.000000 N, and both solvers'
    reaction totals come back 2000.0 in ``z``.

    Element areas come from the shoelace formula on the element's own nodes rather than from
    ``mesh_size**2``: at the coarsest density adapy's mesher puts *four* divisions across the
    0.5 m width on the stiffened strip and *two* on the bare one, so an assumed uniform grid
    would have mis-weighted the coarse bare case by a factor of two and looked like a
    discretisation error.
    """
    tributary: dict[int, float] = collections.defaultdict(float)
    for element in shell_elements(fem):
        points = np.asarray([node.p for node in element.nodes], dtype=float)
        x, y = points[:, 0], points[:, 1]
        area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        for node in element.nodes:
            tributary[node.id] += area / 4.0

    groups: dict[float, list[int]] = collections.defaultdict(list)
    for node_id, area in tributary.items():
        # Rounded to pick the three distinct areas of a structured grid out of float noise;
        # the *emitted* force uses the rounded value so the two sides of the comparison
        # cannot disagree about it by a bit.
        groups[round(area, 12)].append(node_id)
    return tuple(
        NodalLoadGroup(area=area, node_ids=tuple(sorted(groups[area]))) for area in sorted(groups) if area > 0.0
    )


def applied_load_total(groups) -> float:
    """The total ``u3`` force of ``groups``, newtons. Compared against ``-q L b``."""
    return sum(group.force * len(group.node_ids) for group in groups)


def expected_load_total() -> float:
    """``-q L b``: what the pressure integrates to, and what both solvers must react."""
    return -PRESSURE * STRIP_LENGTH * STRIP_WIDTH


def _add_nodal_loads(fem, step) -> None:
    """One ``Load`` per distinct tributary area, over a node set of every node carrying it.

    One per *area* rather than one per node because ``write_loads.load_force`` emits a
    ``BNLOAD`` for every member of the set with the same vector on each -- checked on the
    deck this produces: 165 ``BNLOAD`` records from three ``Load`` objects at the coarsest
    density, summing to -2000.0 N.
    """
    groups = consistent_nodal_loads(fem)
    total = applied_load_total(groups)
    expected = expected_load_total()
    if abs(total - expected) > 1e-09 * abs(expected):
        raise PlateModelInvalid(
            f"the consistent nodal loads sum to {total!r} N and the pressure integrates to "
            f"{expected!r} N. A load vector that does not carry the whole pressure makes every "
            f"displacement below wrong by the same factor, which no comparison between two "
            f"solvers given the same wrong load could see."
        )
    for index, group in enumerate(groups, start=1):
        nodes = [node for node in fem.nodes if node.id in set(group.node_ids)]
        fem_set = fem.add_set(FemSet(f"Q_{index}", nodes, FemSet.TYPES.NSET, parent=fem))
        step.add_load(Load(f"QZ_{index}", Load.TYPES.FORCE, group.force, fem_set=fem_set, dof=[0, 0, 1, 0, 0, 0]))


def _add_pressure(fem, step) -> None:
    """One ``Load`` of type ``pressure`` over the plate's whole shell element set.

    The whole plate, because ``ada.cadit.cae.analysis._pressure_plate`` refuses a set covering
    only part of one: a CAE ``Surface`` is made of whole faces, so a partial set would silently
    become a pressure over all of it.

    ``Load.TYPES`` does not list ``pressure`` -- the constructor sets ``_type`` directly rather
    than through the validating setter, which is why the string is written out here -- so this
    is the one record in either model that adapy itself never builds.
    """
    fem_set = fem.add_set(FemSet("PLATE_SHELLS", shell_elements(fem), FemSet.TYPES.ELSET, parent=fem))
    step.add_load(Load("q", "pressure", PRESSURE, fem_set=fem_set))


def _edge_nset(fem, set_name: str) -> FemSet:
    """The node set one :data:`EDGE_SUPPORTS` entry acts on, by position.

    By position and not by a mesher's own grouping, so the same three sentences describe the
    support at every density and in both decks.
    """
    tol = 1e-09
    if set_name == "SS_X0":
        nodes = [n for n in fem.nodes if abs(n.x) < tol]
    elif set_name == "SS_X1":
        nodes = [n for n in fem.nodes if abs(n.x - STRIP_LENGTH) < tol]
    elif set_name == "CYL":
        nodes = [n for n in fem.nodes if abs(n.y) < tol or abs(n.y - STRIP_WIDTH) < tol]
    else:  # pragma: no cover - EDGE_SUPPORTS is closed
        raise PlateModelInvalid(f"no edge is defined for support set {set_name!r}")
    if not nodes:
        raise PlateModelInvalid(f"support set {set_name!r} matched no node of the mesh")
    return FemSet(set_name, sorted(nodes, key=lambda n: n.id), FemSet.TYPES.NSET, parent=fem)
