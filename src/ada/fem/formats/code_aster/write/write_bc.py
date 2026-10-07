"""Supports and prescribed displacements as ``AFFE_CHAR_MECA``/``DDL_IMPO`` charges.

Code_Aster stops on a dof held by two dualised charges -- measured, 18.1.8: "<ASSEMBLA_26> le noeud: 3 composante: DZ
est bloqué plusieurs fois" for a support's ``DZ=0`` and a settlement's ``DZ`` on the same node, which is how every
support with a prescribed dof reached the deck (the support's ``Bc`` and one per load case on the same set). So a dof
a settlement prescribes (:func:`ada.fem.formats.prescribed.prescribed_dofs`) is left out of the support's charge and
held by exactly one other charge: :data:`PRESCRIBED_AT_ZERO` (every such dof at 0) in a step that gives none of them a
value, or the step's own charge of values (:mod:`.steps.static_lin`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .names import concept_name

if TYPE_CHECKING:
    from ada.fem import Bc

#: The charge holding every prescribed dof at zero, in the steps that give them no value.
PRESCRIBED_AT_ZERO = "prescribed_zero"

_DOF_NAMES = ("DX", "DY", "DZ", "DRX", "DRY", "DRZ")


def _is_solid(fem_set) -> bool:
    from ada.fem.utils import is_parent_of_node_solid

    return any(is_parent_of_node_solid(no) for no in fem_set.members)


def held_dofs(bc: Bc, prescribed) -> list[int]:
    """The dofs a support's charge holds: its own, less those a settlement prescribes (``prescribed``: the keys of
    :func:`ada.fem.formats.prescribed.prescribed_dofs`), and less the rotations of a node set on solid elements."""
    from ada.fem.formats.prescribed import settlements

    if settlements([bc]):
        return []
    top = 3 if _is_solid(bc.fem_set) else 6
    return [d for d in range(1, top + 1) if d in bc.dofs and (bc.fem_set.name, d) not in prescribed]


def create_bc_str(bc: Bc, prescribed=frozenset()) -> str:
    """A support's ``DDL_IMPO`` charge, every dof it holds at 0; empty for a settlement and for a support whose dofs
    are all prescribed (see the module docstring)."""
    dofs = held_dofs(bc, prescribed)
    if not dofs:
        return ""
    bc_str = "".join(f"{_DOF_NAMES[d - 1]}=0, " for d in dofs)
    dofs_str = f"""dofs = dict(
    GROUP_NO="{bc.fem_set.name}",
    {bc_str}
)\n"""

    return (
        dofs_str
        + f"""{concept_name(bc, "bc")} = AFFE_CHAR_MECA(
    MODELE=model, DDL_IMPO=_F(**dofs)
)"""
    )


def prescribed_at_zero_str(bcs) -> str:
    """The :data:`PRESCRIBED_AT_ZERO` charge of the model's ``bcs``; empty when nothing is prescribed."""
    from ada.fem.formats.prescribed import prescribed_dofs

    return prescribed_charge_str(PRESCRIBED_AT_ZERO, {key: 0.0 for key in prescribed_dofs(bcs)}, bcs)


def prescribed_charge_str(name: str, values: dict, bcs) -> str:
    """A ``DDL_IMPO`` charge ``name`` giving each prescribed dof ``(set, dof)`` its value in ``values``. A set on
    solid elements takes no rotation (as :func:`held_dofs`)."""
    sets = {bc.fem_set.name: bc.fem_set for bc in bcs}
    by_set: dict[str, list[str]] = {}
    for (set_name, dof), value in values.items():
        if dof > 3 and _is_solid(sets[set_name]):
            continue
        by_set.setdefault(set_name, []).append(f"{_DOF_NAMES[dof - 1]}={float(value)!r}")
    if not by_set:
        return ""
    rows = "\n".join(f'        _F(GROUP_NO="{s}", {", ".join(v)}),' for s, v in by_set.items())
    return f"""{name} = AFFE_CHAR_MECA(
    MODELE=model,
    DDL_IMPO=(
{rows}
    ),
)"""
