from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada import FEM
from ada.fem.loads import Load
from ada.fem.steps import Step

from .not_held import STAGE, report
from .write_utils import write_ff

if TYPE_CHECKING:
    from .writer import NodeDofs


#: The load case a step's loads go into when the step declares none of its own.
DEFAULT_CASE = "LC1"


def loads_str(fem: FEM, ndofs: NodeDofs | None = None, prescribed: dict[int, dict[int, float]] | None = None) -> str:
    """The load block of ``fem``'s first step.

    ``ndofs`` (:class:`writer.NodeDofs`) is threaded down to BNLOAD, which declares an
    NDOF of its own and must agree with GNODE. Left at ``None`` it is derived from ``fem``.
    """
    from .writer import node_dofs

    step = fem.steps[0] if len(fem.steps) > 0 else None
    return step_loads_str(step, node_dofs(fem) if ndofs is None else ndofs, prescribed)


def _case_str(lid: int, name: str) -> str:
    return write_ff("TDLOAD", [(4, lid, 100 + len(name), 0), (name,)])


def step_loads_str(
    step: Step | None,
    ndofs: NodeDofs | None = None,
    prescribed: dict[int, dict[int, float]] | None = None,
) -> str:
    """The load block of one step: its load cases, or all its loads as one case ``LC1``.

    ``to_fem`` passes the part FEM's dof counts even when the step lives on the assembly,
    because that is where the nodes a load names actually live.

    ``prescribed`` (``{node id: {dof: value}}``, from ``write_bcs.prescribed_displacements``)
    are the settlements, and they are written *here* rather than with the boundary conditions
    because in Sesam a prescribed displacement is loading: its BNDISPL record declares an LLC.
    A model whose only loading is a settlement therefore still needs a load case -- with FIX
    code 2 and no load case at all Sestra V11.3-00 warns "No load is specified" and writes no
    displacement result -- so one is opened for it here.
    """
    from .write_bcs import bndispl_str

    prescribed = prescribed or {}
    if step is None or len(step.loads) == 0:
        if not prescribed:
            return ""
        return _case_str(1, DEFAULT_CASE) + bndispl_str(prescribed, ndofs, 1)

    if len(step.load_cases.keys()) > 0:
        cases = [(lc.name, lc.loads or []) for lc in step.load_cases.values()]
    else:
        cases = [(DEFAULT_CASE, step.loads)]

    out_str = ""
    for lid, (lc_name, loads) in enumerate(cases, start=1):
        out_str += _case_str(lid, lc_name)
        out_str += case_loads_str(loads, lid, ndofs)
        if lid == 1:
            out_str += bndispl_str(prescribed, ndofs, lid)
    if prescribed and len(cases) > 1:
        # A Bc belongs to no load case, so which case a settlement acts in is not something the
        # model says. Sestra solves each case on its own, so putting it in all of them would make
        # every case a settlement case; the first one is the choice, said out loud.
        report().approximated(
            STAGE,
            "BNDISPL",
            cases[0][0],
            "the prescribed displacements are written into the first load case only; each Sesam "
            "load case has its own BNDISPL records and a Bc belongs to no load case",
            n_nodes=len(prescribed),
            n_cases=len(cases),
        )
    return out_str


def case_loads_str(loads, lid: int, ndofs: NodeDofs | None = None) -> str:
    """One load case's records.

    A load case holds one constant of gravity (BGRAV, manual section 7.2.13), so the
    gravity and acceleration loads of a case are summed into a single record -- exact, the
    loads being linear in it. Writing one BGRAV per load, as before, gave a case two
    constants of gravity and left which one wins to the reader.
    """
    gravity = None
    out_str = ""
    for load in loads:
        if load.type in (Load.TYPES.ACC, Load.TYPES.GRAVITY):
            g = _acceleration(load)
            gravity = g if gravity is None else tuple(a + b for a, b in zip(gravity, g))
            _report_amplitude(load)
            continue
        out_str += load_str(load, lid, ndofs)
    if gravity is not None:
        out_str = write_ff("BGRAV", [(lid, 0, 0, 0), gravity]) + out_str
    return out_str


def _report_amplitude(load: Load) -> None:
    if load.amplitude is not None:
        report().approximated(
            STAGE, "Load", load.name, "its amplitude is not written; the load is written at its full magnitude"
        )


def load_str(load: Load, lid, ndofs: NodeDofs | None = None) -> str:
    """One load's records, or ``""`` for a load Sesam has no card for -- which is reported.

    This used to log an error and return ``None`` for anything but gravity and point
    forces, and the caller's string concatenation then raised ``TypeError``: one pressure
    load cost the whole deck.
    """
    rep = report()
    if load.type in [Load.TYPES.ACC, Load.TYPES.GRAVITY]:
        out = load_gravity(load, lid)
    elif load.type == Load.TYPES.FORCE:
        if load.fem_set is None or load.fem_set.type != "nset":
            rep.omitted(STAGE, "Load", load.name, "a point load not on a node set has no BNLOAD form")
            return ""
        if load.csys is not None and _local_axes(load.csys) is None:
            rep.omitted(
                STAGE, "Load", load.name, "a load in a non-rectangular or node-defined coordinate system is not written"
            )
            return ""
        out = load_force(load, lid, ndofs)
        if load.follower_force:
            rep.approximated(STAGE, "Load", load.name, "Sestra is linear; a follower load keeps its direction")
    elif load.type == Load.TYPES.PRESSURE:
        out = load_pressure(load, lid)
        if out == "":
            return ""
    else:
        rep.omitted(STAGE, "Load", load.name, f'a "{load.type}" load is not written by the Sesam writer')
        return ""

    _report_amplitude(load)
    return out


def _acceleration(load: Load) -> tuple[float, float, float]:
    """The acceleration vector BGRAV takes.

    ``Load.acc_vector`` wants exactly one non-``None`` dof entry, but a load read from an
    Abaqus deck names all three direction components (``[1, 0, 0]``), which it rejects. The
    components scaled by the magnitude are the same vector either way.
    """
    if load.type == Load.TYPES.ACC:
        try:
            return tuple(load.acc_vector)
        except ValueError:
            pass
    comps = [0.0 if d is None else float(d) for d in (list(load.dof or [0, 0, 1]) + [0, 0, 0])[:3]]
    return tuple(load.magnitude * c for c in comps)


def load_gravity(load: Load, load_id: int) -> str:
    """Gravity / acceleration field. A ``LoadGravity`` is its magnitude along its dof
    direction (``[0, 0, 1]`` unless given otherwise)."""
    return write_ff(
        "BGRAV",
        [(load_id, 0, 0, 0), _acceleration(load)],
    )


def _local_axes(csys) -> np.ndarray | None:
    """The rows of the local x, y, z unit vectors in global axes, for a rectangular system
    given by coordinates -- the first a point on the local x axis, the second one in the
    local x-y plane, as Abaqus' ``*Transform`` and ``*Orientation`` define them. ``None``
    for any other kind of system.
    """
    if str(csys.system).upper() != "RECTANGULAR" or str(csys.definition).upper() != "COORDINATES":
        return None
    if csys.coords is None or len(csys.coords) < 2:
        return None
    a, b = (np.asarray(v, dtype=float) for v in csys.coords[:2])
    x = a / np.linalg.norm(a)
    z = np.cross(a, b)
    z = z / np.linalg.norm(z)
    return np.array([x, np.cross(z, x), z])


def _global_forces(load: Load) -> list[float]:
    """The load's six components in global axes.

    ``Load.forces_global`` applies the inverse rotation -- a force along local x of a system
    whose x axis is global y came out along *minus* global y -- so the rotation is done
    here: ``f_global = R^T f_local`` with ``R``'s rows the local axes.
    """
    forces = [float(f) for f in load.forces]
    if load.csys is None:
        return forces
    rt = _local_axes(load.csys).T
    return [float(v) for v in np.concatenate([rt @ forces[:3], rt @ forces[3:6]])]


#: BEUSLO's LOTYP for a surface pressure: one intensity per element node, normal to the
#: surface. Measured against Sestra V11.3-00 (see :func:`load_pressure`): LOTYP 2 wants three
#: components per node ("Can only handle vector lengths that are multiples of 3") and LOTYP 3
#: is not a load type at all -- Sestra drops the record and warns "No load is specified".
PRESSURE_LOTYP = 1

#: BEUSLO's SIDE for the face on the element's positive normal, and for the one on its
#: negative normal.
#:
#: Which one is written records the face the model named; it is *not* what sets the direction.
#: Measured, Sestra V11.3-00 accepts 1, 2 and 3 on an FQUS and returns the bit-identical
#: result for all three, because it "computes [BEUSLO loads] as if they act in the
#: middle-plane (for local z = 0) of the element" (Sestra user manual, the FQUS data-type
#: notes). 0 and 4 and up are refused: "Illegal side index specified on BEUSLO record". So the
#: sign of RLOAD is what carries the direction -- see :func:`_pressure_side_and_sign`.
SIDE_POSITIVE = 1
SIDE_NEGATIVE = 2

#: BEUSLO's LAYER. Zero, always: "At least one element load is specified for a layered element
#: (LAYER != 0). Layered elements is not supported in this version. These loads are applied
#: without the layer attribute" -- measured, so a nonzero LAYER buys a warning and nothing else.
PRESSURE_LAYER = 0

#: BEUSLO's INTNO, the integration rule. Zero, the default: "Non-default integration rule
#: (INTNO) specified for at least one element load. Only the default rule is supported in this
#: version. The default rule has been used" -- measured, same story as LAYER.
PRESSURE_INTNO = 0

#: Surface sides that name the face on an element's *negative* normal. The Abaqus reader
#: normalises a single-elset shell surface to ``-1`` (``read_steps``/``reader``), a surface
#: built from nodes with ``shell_positive=False`` carries the same, and a deck that listed its
#: rows by name carries the label itself.
_NEGATIVE_SIDES = frozenset({-1, "SNEG"})

#: ... and the ones that name the face on its positive normal. ``None`` is in here too: a plain
#: element set names no side at all, and Abaqus' own default for a shell surface is SPOS.
_POSITIVE_SIDES = frozenset({None, "", 1, "SPOS"})


def _pressure_region(load: Load):
    """The region a pressure load acts on: a ``Surface`` if it has one, else its ``FemSet``.

    ``LoadPressure`` (what the Abaqus reader builds from ``*Dsload``) carries a ``Surface`` and
    no ``fem_set``; a bare ``Load`` of type ``pressure`` carries an element set. Both reach this
    writer, so both are resolved here rather than at each call site.
    """
    surface = getattr(load, "surface", None)
    return load.fem_set if surface is None else surface


def _pressure_side_and_sign(side) -> tuple[int, float] | None:
    """``(SIDE, sign)`` for one surface side, or ``None`` for a side BEUSLO cannot carry.

    The sign is the whole point. Measured on one S4R with its nodes counter-clockwise in the
    x-y plane (so its normal is +z), Abaqus 2025 puts ``*Dsload P, 1000.`` at U3 =
    -3.2004021E-03 on the ``SPOS`` face and +3.2004021E-03 on the ``SNEG`` face: a positive
    pressure pushes *into* the face it is applied to, which is along the element's **negative**
    normal for the positive face. That is adapy's convention, and every writer has to match it.
    Measured on the same geometry, a positive BEUSLO intensity pushes along the element's normal
    whichever SIDE is written, and reversing the elements' node order reverses it -- so it is the
    element's own normal that sets the direction, not the global axes and not SIDE.

    The two are therefore opposite, and a positive-face pressure is what needs the flip. Writing
    SIDE = 2 for a negative-face one as well keeps the record saying which face the model meant.

    This was the other way round until the convention was checked across formats, and the story of
    how is worth keeping, because nothing in the Sestra runs could see it. The reference the BEUSLO
    sign was tuned against came from ``verification/genie_vs_abaqus``, which applied the same
    pressure as its exact consistent *nodal* load, with the force ``-PRESSURE * area`` along
    ``dof=[0, 0, 1]`` -- global **-z** -- on the stated grounds that this was "against the plate's
    ``+z`` normal". The plate's normal is not +z: gmsh winds every element of that strip with its
    normal along **-z** (measured, all 128 of them, and identically for the ``"line"`` and
    ``"shell"`` bm_repr the two builders used). So the reference load ran *along* the element normal
    rather than into the positive face, the BEUSLO sign was matched to it, and the test that pinned
    it compared BEUSLO against that same nodal reference -- which makes it a check that BEUSLO
    reproduces a downward load, not a check of which face the model named. The face mapping was
    never pinned, which is why this survived.

    What settled it, without a Sestra licence: the same strip through the two solvers that can be
    run freely. A positive pressure on the positive face gives mid-span ``u3`` = **+0.17306500**
    from ccx 2.23 and **+0.17319792** from Code_Aster 17.3 -- both +z, i.e. into the positive face,
    agreeing with Abaqus. Code_Aster's value is the Sestra reference's magnitude to eight digits
    (0.1731979101896286) with the opposite sign, which is the measurement that says the two decks
    are the same discretisation and differ only here. See
    ``tests/fem/test_pressure_load_cross_format.py``.
    """
    if side in _POSITIVE_SIDES:
        return SIDE_POSITIVE, -1.0
    if side in _NEGATIVE_SIDES:
        return SIDE_NEGATIVE, 1.0
    return None


def load_pressure(load: Load, load_id: int) -> str:
    """One BEUSLO per shell element of the load's surface, or ``""`` -- which is reported.

    The record, which is not read off a manual -- no Input Interface File manual ships with the
    installed Sestra -- but off Sestra V11.3-00 and DNV's own SIF type definitions:

    ``BEUSLO   LLC LOTYP COMPLX LAYER / ELNO NDOF INTNO SIDE / RLOAD1..RLOADn``

    Every intensity is the pressure itself, one per node of the element (``NDOF`` = the node
    count), not a force and not a per-node share of one: measured, NDOF = 1 on a 4-noded FQUS is
    refused with "Load intensity vector size does not match dof count for load".

    ``ELNO`` is the *internal* element number (Sestra's own accessor is
    ``BeusloReader::GetInternalElementId``). ``write_elements`` writes GELMNT1 with ELNOX = ELNO
    = the model's own element id, so the two are the same number here.

    Verified against Sestra on a 4.0 x 0.5 m, 10 mm simply supported strip in cylindrical
    bending under 1000 Pa (``tests/fem/test_sesam_pressure_load.py``): the BEUSLO deck and a
    deck carrying the exact consistent nodal load (``q A / 4`` at each node of a bilinear quad)
    return the **bit-identical** mid-span deflection at three mesh densities --
    -0.1731979101896286 / -0.17329947650432587 / -0.17332486808300018 at 32 / 64 / 128 elements
    per span -- converging at order 4.00 on the closed form ``5 q L^4 / (384 D)`` =
    0.1733333333. So BEUSLO's own load integration *is* the consistent load vector, and the
    summed reaction is 2000.0 N against ``q L b`` = 2000 exactly.

    What is refused rather than approximated, each by name: a pressure on beams, solids, point
    masses or springs (BEUSLO's SIDE numbering for a solid face is not established here, and the
    others have no surface at all), on a node-based surface (BEUSLO names an element and a side),
    on an element with no Sesam element type, and a ``LoadPressure`` whose magnitude is a total
    force rather than a pressure.
    """
    from ada.fem.shapes.definitions import ShellShapes

    rep = report()
    region = _pressure_region(load)
    if region is None:
        rep.omitted(STAGE, "Load", load.name, "a pressure load naming neither a surface nor an element set")
        return ""

    distribution = getattr(load, "distribution", None)
    if distribution is not None and distribution != "uniform":
        rep.omitted(
            STAGE, "Load", load.name, f'a "{distribution}" pressure is not a pressure intensity; BEUSLO takes one'
        )
        return ""

    groups = _pressure_groups(region)
    if groups is None:
        rep.omitted(STAGE, "Load", load.name, "a pressure on a node-based surface has no BEUSLO form")
        return ""

    out = ""
    refused: dict[str, list[str]] = {}
    for members, side in groups:
        resolved = _pressure_side_and_sign(side)
        # Sorted by element id: a set is a list and a surface may name several, and the deck
        # must not depend on which order they came out of the model.
        for el in sorted(members, key=lambda e: e.id):
            why = _pressure_refusal(el, resolved, ShellShapes)
            if why is not None:
                refused.setdefault(why, []).append(str(el.id))
                continue
            beuslo_side, sign = resolved
            intensity = sign * float(load.magnitude)
            out += write_ff(
                "BEUSLO",
                [
                    (load_id, PRESSURE_LOTYP, 0, PRESSURE_LAYER),
                    (el.id, len(el.nodes), PRESSURE_INTNO, beuslo_side),
                    tuple([intensity] * len(el.nodes)),
                ],
            )
    for why, ids in sorted(refused.items()):
        rep.omitted(STAGE, "Load", load.name, why, elements=sorted(ids, key=int)[:10], n_elements=len(ids))
    return out


def _pressure_groups(region):
    """The region's ``(members, side)`` groups, or ``None`` if it is node-based.

    ``_region_groups`` is what :func:`ada.fem.surfaces.surface_nodes` resolves a surface with,
    so a pressure and a constraint see the same region; it keeps each set's side beside its
    members, which is exactly what a per-element surface load needs. A ``FemSet`` handed over
    directly comes back as one group with no side.
    """
    from ada.fem.sets import FemSet
    from ada.fem.surfaces import Surface, _region_groups

    if isinstance(region, Surface) and region.type == Surface.TYPES.NODE:
        return None
    if isinstance(region, FemSet) and region.type != FemSet.TYPES.ELSET:
        return None
    return _region_groups(region)


def _pressure_refusal(el, resolved, shell_shapes) -> str | None:
    """Why BEUSLO cannot carry a pressure on this element, or ``None`` if it can."""
    from ..common import sesam_reverse

    el_type = getattr(el, "type", None)
    if el_type is None:
        # A node, from an ELEMENT-typed surface built over a node set. Refused rather than
        # crashed on: ``el.type`` used to raise AttributeError here and lose the whole deck.
        return "a pressure on a node; BEUSLO names an element and a side"
    if not isinstance(el_type, shell_shapes):
        return (
            f"a pressure on a {el_type} element has no BEUSLO form; BEUSLO is a shell surface load "
            "(the Sesam SIDE numbering of a solid face is not established here)"
        )
    if el_type not in sesam_reverse:
        return f"a pressure on a {el_type} element, which has no Sesam element type and is not in the deck"
    if resolved is None:
        return "a pressure on a surface side that is not a shell face (SPOS / SNEG)"
    return None


def load_force(load: Load, load_id: int, ndofs: NodeDofs | None = None) -> str:
    """One BNLOAD per node of the load's node set.

    Written in global axes: a load given in a local coordinate system is rotated into the
    global one (:func:`_global_forces`). The writer used to write its local components as
    though they were global, and only on the set's first node.

    BNLOAD's NDOF has to match the node's GNODE, so a 3-dof node — one touched only by
    solid elements — gets ``3`` and its three force components. A nonzero *moment* on such
    a node raises: there is no rotational dof for it to act on, and writing it would be a
    load Sestra silently loses at best.
    """
    from .writer import ALL_SIX_DOF

    if ndofs is None:
        ndofs = ALL_SIX_DOF

    lotype = 0
    complx = 0  # Assumed no phase shift
    forces = _global_forces(load)
    out = ""
    for node in load.fem_set.members:
        node_no = node.id
        ndof = ndofs.ndof(node_no)
        moments = [dof for dof in (4, 5, 6) if dof > ndof and forces[dof - 1] != 0.0]
        if moments:
            raise ValueError(
                f'sesam writer: load "{load.name}" applies a moment on dof(s) {moments} of node {node_no}, '
                f"which has {ndof} dofs (NDOF={ndof}). A node attached only to solid elements has no "
                "rotational dofs for a moment to act on."
            )
        real_loads_1 = tuple([node_no, ndof] + forces[:2])
        real_loads_2 = tuple(forces[2:ndof])
        out += write_ff(
            "BNLOAD",
            [(load_id, lotype, complx, 0), real_loads_1, real_loads_2],
        )
    return out
