from __future__ import annotations

from typing import TYPE_CHECKING

from ada import FEM
from ada.fem.shapes.definitions import MassTypes

from .not_held import STAGE, report
from .write_utils import write_ff

if TYPE_CHECKING:
    from .writer import NodeDofs


def _bnmass_components(mass, n_members: int) -> list[float]:
    """Six BNMASS components [m1..m6] for a Mass element. Per the Sesam Input Interface File
    spec, BNMASS gives mass per DOF: 1-3 translational, 4-6 rotational (rotary inertia about
    x/y/z). A scalar value applies to all three of its DOFs; a list maps to the first three.

    - MASS          -> translational (DOF 1-3); isotropic scalar or anisotropic 3-vector.
    - ROTARYI       -> rotary inertia (DOF 4-6); only the diagonal I11/I22/I33 is kept,
                       BNMASS has no off-diagonal terms.
    - NONSTRUCTURAL -> a region mass lumped equally onto its member nodes (total / N) as
                       translational mass — BNMASS is nodal, there is no distributed form.
    """
    raw = mass._mass
    vals = [float(x) for x in (raw if isinstance(raw, (list, tuple)) else [raw])]
    tri = (vals * 3)[:3] if len(vals) == 1 else vals[:3]
    comps = [0.0] * 6
    mt = mass.type
    if mt == MassTypes.MASS:
        comps[0:3] = tri
    elif mt == MassTypes.ROTARYI:
        comps[3:6] = tri
    elif mt == MassTypes.NONSTRUCTURAL:
        per = (vals[0] / n_members) if n_members else vals[0]
        comps[0:3] = [per, per, per]
    else:
        raise NotImplementedError(f"Mass type {mt} is not yet supported")
    return comps


def mass_str(fem: FEM, ndofs: NodeDofs | None = None) -> str:
    """The BNMASS block.

    BNMASS declares an NDOF of its own and then lists exactly that many mass components,
    so it has to agree with what GNODE says about the node (:class:`writer.NodeDofs`): a
    node touched only by solid elements is 3-dof and gets ``3`` plus its three
    translational components. A rotary inertia on such a node is a contradiction — the
    node has no rotational dofs to carry it — and raises, rather than being quietly
    dropped or written as a record the GNODE block disagrees with.

    A purely translational mass never promotes a node to 6 dofs; see
    ``writer._carries_rotational_stiffness``. Left at ``None``, the dof counts are derived
    from ``fem``.
    """
    from .writer import node_dofs

    if ndofs is None:
        ndofs = node_dofs(fem)

    rep = report()
    out_str = ""

    for mass in fem.elements.masses:
        members = list(mass.members)
        if mass.type == MassTypes.NONSTRUCTURAL:
            if any(getattr(m, "nodes", None) is not None for m in members):
                # An element region: its members are elements, whose ids this loop used to
                # write into BNMASS's *node* field, and the value is a mass per length/area/
                # volume, not a total. BNMASS has no distributed form.
                rep.omitted(STAGE, "Mass", mass.name, "a non-structural mass over elements has no nodal BNMASS form")
                continue
            rep.approximated(
                STAGE, "Mass", mass.name, "a non-structural mass is lumped equally onto its nodes", n_nodes=len(members)
            )
        elif mass.type == MassTypes.ROTARYI:
            raw = mass._mass
            if isinstance(raw, (list, tuple)) and any(float(x) != 0.0 for x in raw[3:]):
                rep.approximated(
                    STAGE, "Mass", mass.name, "BNMASS has no products of inertia; only I11, I22, I33 are written"
                )
        comps = _bnmass_components(mass, max(1, len(members)))
        for m in members:
            ndof = ndofs.ndof(m.id)
            rotational = [dof for dof in (4, 5, 6) if dof > ndof and comps[dof - 1] != 0.0]
            if rotational:
                raise ValueError(
                    f'sesam writer: mass "{mass.name}" puts rotary inertia on dof(s) {rotational} of '
                    f"node {m.id}, which has {ndof} dofs (NDOF={ndof}). A node attached only to solid "
                    "elements has no rotational dofs to carry it."
                )
            data = (tuple([m.id, ndof] + comps[:2]), tuple(comps[2:ndof]))
            out_str += write_ff("BNMASS", data)
    return out_str


def write_node_with_mass_point():
    raise NotImplementedError()
