"""Constraints in a CalculiX deck.

The writer used to drop every constraint without a word, so a model coupled to a reference node -- a beam-end
support through ``section_support="coupled"``, say -- was written as a floating reference node and an unsupported
structure: every eigenfrequency came out at zero. A constraint is now written, or refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import Constraint
from ada.fem.exceptions import IncompatibleElements

if TYPE_CHECKING:
    from ada import Assembly, Part


def constraints_str(part: Part, assembly: Assembly) -> str:
    constraints = list(part.fem.constraints.values())
    if assembly is not part:
        constraints += list(assembly.fem.constraints.values())
    if len(constraints) == 0:
        return "** No Constraints"
    return "\n".join(constraint_str(c) for c in constraints)


def constraint_str(constraint: Constraint) -> str:
    if constraint.type == Constraint.TYPES.COUPLING:
        return _coupling(constraint)
    raise IncompatibleElements(
        f'Calculix writer: constraint "{constraint.name}" is of type {constraint.type}, which it cannot write yet. '
        "Writing the deck without it would describe a different model."
    )


def _coupling(constraint: Constraint) -> str:
    """A kinematic coupling: the nodes of ``s_set`` follow the reference node of ``m_set`` in the given dofs.

    ``*COUPLING`` takes a surface, and adapy's coupling holds a node set, so a node surface over that set is
    written alongside it -- as the Abaqus writer does.
    """
    coupling_type = constraint.metadata.get("coupling_type", "kinematic")
    if coupling_type != "kinematic":
        raise IncompatibleElements(
            f'Calculix writer: coupling "{constraint.name}" is {coupling_type}; only kinematic couplings are '
            "written (a distributing coupling needs an element face surface)"
        )
    if any(_on_shell(n) for n in constraint.s_set.members):
        # CalculiX expands shells into solids and builds a kinematic coupling as a RIGID MPC, which it refuses on
        # shell nodes ("*ERROR in gen3dmpc: nodes belonging to shell elements must not be subject to a RIGID MPC").
        raise IncompatibleElements(
            f'Calculix writer: coupling "{constraint.name}" ties shell nodes, which CalculiX cannot couple '
            "(its kinematic coupling is a RIGID MPC, refused on shell nodes). For a beam-end support use "
            'section_support="direct".'
        )
    surf_name = f"{constraint.name}_surf"
    ref_node = constraint.m_set.members[0].id
    # CalculiX's *KINEMATIC takes the translational dofs only ("*ERROR reading *KINEMATIC" on 4-6): the
    # reference node's rotations act on the coupled nodes through the rigid-body kinematics it builds itself.
    dofs = sorted({d for d in _expand(constraint.dofs) if d <= 3})
    dof_lines = [f"{d}, {d}" for d in dofs]
    return "\n".join(
        [
            f"** Coupling {constraint.name}",
            f"*SURFACE, NAME={surf_name}, TYPE=NODE",
            f"{constraint.s_set.name}",
            f"*COUPLING, CONSTRAINT NAME={constraint.name}, REF NODE={ref_node}, SURFACE={surf_name}",
            "*KINEMATIC",
            *dof_lines,
        ]
    )


def _on_shell(node) -> bool:
    from ada.fem import Elem

    return any(isinstance(r, Elem) and isinstance(r.type, Elem.EL_TYPES.SHELL_SHAPES) for r in node.refs)


def _expand(dofs) -> list[int]:
    """A constraint's dofs as single integers: an entry is a dof, or a (first, last) range."""
    out = []
    for d in dofs:
        if isinstance(d, int):
            out.append(d)
        else:
            out.extend(range(int(d[0]), int(d[1]) + 1))
    return out
