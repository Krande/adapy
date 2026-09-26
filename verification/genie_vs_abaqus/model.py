"""The concept model, written once, from which every solver input is derived.

A **portal frame**: two columns, a girder, fixed bases, and a pair of horizontal point
loads at the two top corners. Deliberately the simplest structure that still exercises
what the comparison is about -- joint continuity, bending, and a translated support and
load -- because a discrepancy in it is attributable to one member or one record, and
because it has a closed-form answer (:mod:`hand_check`).

Three modelling choices are deliberate and worth defending:

**A rotationally symmetric section** (``OD200x10``, a 200 mm x 10 mm tube).
``Iy == Iz``, so the answer does not depend on how either writer resolves the member's
local axes. Beam orientation -- Sesam's ``GUNIVEC``/``TRANSNO`` versus an Abaqus ``n1``
vector -- is a real and separate translation risk, and mixing it into the first
comparison would make a disagreement unattributable. An orientation-sensitive section
(an I-profile in the frame plane) is the natural follow-up once this closes.

**Slender members**: ``h/D = 30``. Transverse shear contributes 0.63% of the sway (see
:func:`hand_check.sway_timoshenko`), so the two solvers' differing definitions of a
tube's shear area can move the answer by at most a couple of tenths of a percent. That is
what keeps the comparison a test of the translation rather than a test of two beam
formulations.

**A symmetric pair of loads**, ``P/2`` at each top corner rather than ``P`` at one. With
the load split the frame's response is exactly antisymmetric: the girder carries no axial
force, both top corners sway by the same amount, and the closed form in :mod:`hand_check`
is exact rather than approximate. Loading one corner only puts ``P/2`` of axial force
through the girder, which makes the two corners differ by ``P*L/(2*E*A)`` and turns the
hand check into a four-degree-of-freedom problem -- a worse reference, for no gain in
realism.

Probe points (:data:`PROBE_POINTS`) are member ends, mid-spans and girder quarter points.
Every one of them sits at an integer multiple of :data:`MESH_SIZE` from a member end, so
any mesher that seeds member ends and uses this target size puts a node exactly there.
:func:`assert_probes_are_seeded` checks that rather than assuming it.
"""

from __future__ import annotations

from dataclasses import dataclass

import ada
from ada.fem import Bc, FemSet, Load, StepImplicitStatic
from ada.materials.metals import CarbonSteel

#: Frame height, metres. Column base to girder centreline.
HEIGHT = 6.0

#: Frame span, metres. Centreline to centreline of the two columns.
SPAN = 8.0

#: Both columns and the girder. ``OD<outer diameter mm>x<wall mm>`` -- a tube, so
#: ``Iy == Iz`` and the result cannot depend on beam orientation. See the module
#: docstring.
SECTION = "OD200x10"

#: S355: E = 210 GPa, nu = 0.3, rho = 7850 kg/m3 (adapy's ``CarbonSteel`` defaults,
#: written to the Sesam deck as ``MISOSEL``).
MATERIAL_NAME = "S355"

#: Total horizontal load, newtons, applied as :data:`P_TOTAL` / 2 at each top corner.
#: 10 kN gives ~24.7 mm of sway: four orders of magnitude above the single-precision
#: resolution of a Sesam ``.SIN``, and 0.4% of the frame height, so a geometrically
#: linear analysis is unambiguously valid.
P_TOTAL = 10.0e3

#: Target element size, metres. Divides both :data:`HEIGHT` (6 elements) and :data:`SPAN`
#: (8 elements) into an even count, which is what puts a node at every mid-span probe. Do
#: not change it without re-running :func:`assert_probes_are_seeded`.
MESH_SIZE = 1.0

#: Name of the single load case / step.
LOAD_CASE = "LC1"


@dataclass(frozen=True)
class ProbePoint:
    """A point in space at which both solvers are asked for a displacement.

    Identified by *position*, not by node id: the two solvers mesh independently and
    their node numberings have nothing to do with each other. See
    :func:`displacements.sample_fea_result`.
    """

    name: str
    xyz: tuple[float, float, float]
    description: str


#: The points compared between solvers. Member ends and mid-spans, plus the girder
#: quarter points -- which is where the antisymmetric S-shaped girder deflection peaks and
#: is therefore the most sensitive discriminator of joint-rotation continuity in the whole
#: model.
#:
#: The two base points are included on purpose even though they are identically zero: they
#: are how the comparison sees that the fixed supports survived translation at all. An
#: all-zero table is *not* allowed to pass -- see :func:`compare.assert_has_signal`.
PROBE_POINTS: tuple[ProbePoint, ...] = (
    ProbePoint("BASE_L", (0.0, 0.0, 0.0), "left column base, fully fixed"),
    ProbePoint("BASE_R", (SPAN, 0.0, 0.0), "right column base, fully fixed"),
    ProbePoint("COL_L_MID", (0.0, 0.0, HEIGHT / 2), "left column mid-height"),
    ProbePoint("COL_R_MID", (SPAN, 0.0, HEIGHT / 2), "right column mid-height"),
    ProbePoint("TOP_L", (0.0, 0.0, HEIGHT), "left top corner, loaded, sway probe"),
    ProbePoint("TOP_R", (SPAN, 0.0, HEIGHT), "right top corner, loaded, sway probe"),
    ProbePoint("GIRDER_QTR", (SPAN / 4, 0.0, HEIGHT), "girder quarter point, S-curve peak"),
    ProbePoint("GIRDER_MID", (SPAN / 2, 0.0, HEIGHT), "girder mid-span"),
    ProbePoint("GIRDER_3QTR", (3 * SPAN / 4, 0.0, HEIGHT), "girder three-quarter point, S-curve peak"),
)

#: The probe whose displacement the hand check predicts.
SWAY_PROBE = "TOP_L"


def build_portal_frame() -> ada.Assembly:
    """The one definition of the model. Both solver inputs are derived from this.

    Returns an :class:`ada.Assembly` carrying:

    * a line (beam-element) FEM on the ``PortalFrame`` part, meshed at :data:`MESH_SIZE`;
    * node sets ``BASE_L``, ``BASE_R``, ``TOP_L``, ``TOP_R``;
    * two ``Bc`` records fixing all six DOFs at the bases;
    * one ``StepImplicitStatic`` carrying two nodal force loads of :data:`P_TOTAL` / 2 in
      global X.

    The Abaqus half must call *this* function and derive its CAE geometry from the same
    concept beams, so that a disagreement is a translation difference and not two
    different structures.
    """
    mat = ada.Material(MATERIAL_NAME, CarbonSteel(MATERIAL_NAME))
    col_l = ada.Beam("COL_L", (0, 0, 0), (0, 0, HEIGHT), SECTION, mat)
    col_r = ada.Beam("COL_R", (SPAN, 0, 0), (SPAN, 0, HEIGHT), SECTION, mat)
    girder = ada.Beam("GIRDER", (0, 0, HEIGHT), (SPAN, 0, HEIGHT), SECTION, mat)

    part = ada.Part("PortalFrame") / (col_l, col_r, girder)
    part.fem = part.to_fem_obj(MESH_SIZE, "line")

    base_l = part.fem.add_set(_nset(part.fem, "BASE_L", (0.0, 0.0, 0.0)))
    base_r = part.fem.add_set(_nset(part.fem, "BASE_R", (SPAN, 0.0, 0.0)))
    top_l = part.fem.add_set(_nset(part.fem, "TOP_L", (0.0, 0.0, HEIGHT)))
    top_r = part.fem.add_set(_nset(part.fem, "TOP_R", (SPAN, 0.0, HEIGHT)))

    # Fully fixed bases. The Sesam writer emits these as BNBCD FIX code 1 on all six
    # dofs; ada carries no magnitude for a Bc, so "fixed" is the only support this
    # comparison can use -- see sestra_runner for the prescribed-displacement gap.
    part.fem.add_bc(Bc("FIX_L", base_l, [1, 2, 3, 4, 5, 6]))
    part.fem.add_bc(Bc("FIX_R", base_r, [1, 2, 3, 4, 5, 6]))

    assembly = ada.Assembly("PortalSite") / part
    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    # One Load per node, not one Load over a two-node set: the Sesam writer's
    # ``write_loads.load_force`` reads ``load.fem_set.members[0]`` and ignores the rest,
    # so a set-wide force load would put half the intended load on the frame. See
    # sestra_runner's "what adapy can and cannot write" note.
    step.add_load(Load("PX_L", Load.TYPES.FORCE, P_TOTAL / 2, fem_set=top_l, dof=[1, 0, 0, 0, 0, 0]))
    step.add_load(Load("PX_R", Load.TYPES.FORCE, P_TOTAL / 2, fem_set=top_r, dof=[1, 0, 0, 0, 0, 0]))

    return assembly


def section_properties() -> dict[str, float]:
    """The cross-section and material numbers the hand check needs.

    Read off the *model*, not retyped, so the closed form and the solver are fed the same
    section. ``shear_area`` is ``Shary``, which is what adapy writes into the Sesam
    ``GBEAMG`` record's ``SHARY``/``SHARZ`` fields, i.e. what Sestra actually uses for
    transverse shear -- not a textbook shear factor.
    """
    mat = ada.Material(MATERIAL_NAME, CarbonSteel(MATERIAL_NAME))
    bm = ada.Beam("probe", (0, 0, 0), (1, 0, 0), SECTION, mat)
    props = bm.section.properties
    model = bm.material.model
    return {
        "E": float(model.E),
        "nu": float(model.v),
        "area": float(props.Ax),
        "Iy": float(props.Iy),
        "Iz": float(props.Iz),
        "shear_area": float(props.Shary),
    }


def assert_probes_are_seeded(fem) -> None:
    """Raise unless every :data:`PROBE_POINTS` coordinate is an actual node of ``fem``.

    Checked rather than assumed: the probes are the whole comparison, and a mesher that
    quietly seeded 7 elements into a 6 m column would leave the mid-height probe without a
    node. Called by the Sestra runner; the Abaqus half should call it too.
    """
    missing = []
    for probe in PROBE_POINTS:
        if len(fem.nodes.get_by_volume(p=probe.xyz, tol=1e-6)) != 1:
            missing.append(f"{probe.name} at {probe.xyz}")
    if missing:
        raise ValueError(
            "the mesh has no unique node at these probe points, so the comparison would be "
            f"silently sampling the wrong place: {missing}. MESH_SIZE={MESH_SIZE} must divide "
            f"HEIGHT={HEIGHT} and SPAN={SPAN} into an even number of elements."
        )


def _nset(fem, name: str, p: tuple[float, float, float]) -> FemSet:
    nodes = fem.nodes.get_by_volume(p=p, tol=1e-6)
    if len(nodes) != 1:
        raise ValueError(f"expected exactly one node at {p} for set {name}, found {len(nodes)}")
    return FemSet(name, list(nodes), FemSet.TYPES.NSET, parent=fem)
