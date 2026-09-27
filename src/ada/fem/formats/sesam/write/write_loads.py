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
