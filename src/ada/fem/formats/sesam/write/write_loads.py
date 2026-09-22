from __future__ import annotations

from typing import TYPE_CHECKING

from ada import FEM
from ada.config import logger
from ada.fem.loads import Load, LoadGravity

from .write_utils import write_ff

if TYPE_CHECKING:
    from .writer import NodeDofs


def loads_str(fem: FEM, ndofs: NodeDofs | None = None) -> str:
    """The load block.

    ``ndofs`` (:class:`writer.NodeDofs`) is threaded down to BNLOAD, which declares an
    NDOF of its own and must agree with GNODE. ``to_fem`` passes the part FEM's dof counts
    even when writing the assembly FEM's loads, because that is where the nodes a load
    names actually live; left at ``None`` they are derived from ``fem``.
    """
    from .writer import node_dofs

    if len(fem.steps) == 0:
        return ""
    step = fem.steps[0]
    if len(step.loads) == 0:
        return ""

    if ndofs is None:
        ndofs = node_dofs(fem)

    if len(step.load_cases.keys()) > 0:
        out_str = ""
        for i, lc in enumerate(step.load_cases.values(), start=1):
            out_str += write_ff("TDLOAD", [(4, i, 100 + len(lc.name), 0), (lc.name,)])
            for load in lc.loads:
                out_str += load_str(load, i, ndofs)
        return out_str

    lid = 1
    load_case_name = "LC1"
    out_str = write_ff("TDLOAD", [(4, lid, 100 + len(load_case_name), 0), (load_case_name,)])
    for load in step.loads:
        out_str += load_str(load, lid, ndofs)

    return out_str


def load_str(load: Load, lid, ndofs: NodeDofs | None = None):
    if load.type in [Load.TYPES.ACC, Load.TYPES.GRAVITY]:
        return load_gravity(load, lid)
    elif load.type == Load.TYPES.FORCE:
        return load_force(load, lid, ndofs)
    else:
        logger.error(f'Unsupported Load type "{load.type}"')


def load_gravity(load: Load, load_id: int) -> str:
    """Gravity Acceleration field"""
    if isinstance(load, LoadGravity):
        load_vector = tuple([0, 0, load.magnitude])
    else:
        load_vector = tuple(load.acc_vector)
    return write_ff(
        "BGRAV",
        [(load_id, 0, 0, 0), load_vector],
    )


def load_force(load: Load, load_id: int, ndofs: NodeDofs | None = None) -> str:
    """Node with load.

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
    node_no = load.fem_set.members[0].id
    forces = load.forces
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
    return write_ff(
        "BNLOAD",
        [(load_id, lotype, complx, 0), real_loads_1, real_loads_2],
    )
