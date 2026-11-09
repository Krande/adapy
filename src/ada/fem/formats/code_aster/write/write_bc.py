"""Supports and prescribed displacements as ``AFFE_CHAR_MECA``/``DDL_IMPO`` charges.

Code_Aster stops on a dof held by two dualised charges -- measured, 18.1.8: "<ASSEMBLA_26> le noeud: 3 composante: DZ
est bloqué plusieurs fois" for a support's ``DZ=0`` and a settlement's ``DZ`` on the same node, which is how every
support with a prescribed dof reached the deck (the support's ``Bc`` and one per load case on the same set). So a dof
a settlement prescribes (:func:`ada.fem.formats.prescribed.prescribed_dofs`) is left out of the support's charge and
held by exactly one other charge: :data:`PRESCRIBED_AT_ZERO` (every such dof at 0) in a step that gives none of them a
value, or the step's own charge of values (:mod:`.steps.static_lin`).

Two supports whose node sets share a node (a plate's corner on two edges' supports) held that node's common dofs twice
too: "<ASSEMBLA_26> le noeud: 2 composante: DY est bloqué plusieurs fois" (measured, a strip's x = 0 edge held in dy and
its y = 0 edge in dy). Within one ``AFFE_CHAR_MECA`` a dof given twice is held once (measured: the same strip with every
support an ``_F`` of one charge solves, reactions 2000 N = q A to 4e-16), so every support goes into the one charge
:data:`SUPPORTS`: each node is held in the union of the dofs its supports hold, all at zero. A prescribed dof sharing
a node with another support or another settlement cannot be merged -- one value would have to win -- and is refused
by name (:func:`check_overlaps`).

A support in a rotated frame (``Bc.csys``, :mod:`ada.fem.frames`) whose frame is global axes in another order reaches
here as the global support it is (:func:`.write_constraints.model_bcs`); one in a frame that mixes them is a
``LIAISON_OBLIQUE`` row of the same charge, its dofs along the axes ``ANGL_NAUT`` turns to (U4.44.01;
:func:`ada.fem.frames.nautical_angles`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem import Bc

#: The charge holding every prescribed dof at zero, in the steps that give them no value.
PRESCRIBED_AT_ZERO = "prescribed_zero"

#: The one charge holding every support's dofs at zero.
SUPPORTS = "supports"

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


def _angles(bc) -> str | None:
    """``(a, b, g)``, the ``ANGL_NAUT`` of a support in a frame that mixes the global axes, else None."""
    from ada.fem.frames import bc_axes, nautical_angles

    axes = bc_axes(bc)
    if axes is None:
        return None
    return "({:.12g}, {:.12g}, {:.12g})".format(*(a + 0.0 for a in nautical_angles(axes)))


def _charge(name: str, ddl: list[str], oblique: list[str]) -> str:
    if not ddl and not oblique:
        return ""
    keywords = ""
    for keyword, rows in (("DDL_IMPO", ddl), ("LIAISON_OBLIQUE", oblique)):
        if rows:
            body = "\n".join(rows)
            keywords += f"""
    {keyword}=(
{body}
    ),"""
    return f"""{name} = AFFE_CHAR_MECA(
    MODELE=model,{keywords}
)"""


def supports_str(bcs, prescribed=frozenset(), name: str = SUPPORTS) -> str:
    """The :data:`SUPPORTS` charge (or one named ``name``): one ``DDL_IMPO`` row per support, every dof it holds at 0
    (see the module docstring), a ``LIAISON_OBLIQUE`` row for one in a rotated frame; empty when no support holds
    anything."""
    ddl, oblique = [], []
    for bc in bcs:
        dofs = held_dofs(bc, prescribed)
        if dofs:
            held = ", ".join(f"{_DOF_NAMES[d - 1]}=0.0" for d in dofs)
            angles = _angles(bc)
            if angles is None:
                ddl.append(f'        _F(GROUP_NO="{bc.fem_set.name}", {held}),  # {bc.name}')
            else:
                oblique.append(f'        _F(GROUP_NO="{bc.fem_set.name}", ANGL_NAUT={angles}, {held}),  # {bc.name}')
    return _charge(name, ddl, oblique)


def check_overlaps(bcs, prescribed, writer: str = "code_aster") -> None:
    """Raise :class:`~ada.fem.exceptions.model_definition.ConflictingBoundaryConditions` for a prescribed dof of a
    node another support holds, or another settlement's set prescribes, in the same dof.

    Code_Aster stops on such a dof (<ASSEMBLA_26>: held by the support's charge and by the settlement's), and merging
    them would need one value to win: the support's 0 or the settlement's value, or one settlement's over another's.
    The model should give that node's dof one condition. ``writer`` names the writer in the error; the CalculiX
    writer refuses the same models (there the step's value would replace the hold without a word)."""
    from ada.fem.exceptions.model_definition import ConflictingBoundaryConditions
    from ada.fem.formats.prescribed import settlements

    if not prescribed:
        return
    holders: dict[tuple[int, int], list[str]] = {}
    for bc in bcs:
        for d in held_dofs(bc, prescribed):
            for no in bc.fem_set.members:
                holders.setdefault((no.id, d), []).append(bc.name)
    seen: dict[tuple[int, int], tuple[str, str]] = {}
    for bc in settlements(bcs):
        for d in bc.dofs:
            if d is None:
                continue
            for no in bc.fem_set.members:
                key = (no.id, int(d))
                other = holders.get(key)
                if other:
                    raise ConflictingBoundaryConditions(
                        no.id, _DOF_NAMES[int(d) - 1], bc.name, other[0], "holds it at 0", writer
                    )
                if key in seen and seen[key][0] != bc.fem_set.name:
                    raise ConflictingBoundaryConditions(
                        no.id,
                        _DOF_NAMES[int(d) - 1],
                        bc.name,
                        seen[key][1],
                        "prescribes it through another set",
                        writer,
                    )
                seen.setdefault(key, (bc.fem_set.name, bc.name))


def prescribed_at_zero_str(bcs) -> str:
    """The :data:`PRESCRIBED_AT_ZERO` charge of the model's ``bcs``; empty when nothing is prescribed."""
    from ada.fem.formats.prescribed import prescribed_dofs

    return prescribed_charge_str(PRESCRIBED_AT_ZERO, {key: 0.0 for key in prescribed_dofs(bcs)}, bcs)


def prescribed_charge_str(name: str, values: dict, bcs) -> str:
    """A ``DDL_IMPO`` charge ``name`` giving each prescribed dof ``(set, dof)`` its value in ``values``. A set on
    solid elements takes no rotation (as :func:`held_dofs`)."""
    from ada.fem.formats.prescribed import settlements

    sets = {bc.fem_set.name: bc.fem_set for bc in bcs}
    frames = {bc.fem_set.name: _angles(bc) for bc in settlements(bcs)}
    by_set: dict[str, list[str]] = {}
    for (set_name, dof), value in values.items():
        if dof > 3 and _is_solid(sets[set_name]):
            continue
        by_set.setdefault(set_name, []).append(f"{_DOF_NAMES[dof - 1]}={float(value)!r}")
    ddl, oblique = [], []
    for s, v in by_set.items():
        angles = frames.get(s)
        if angles is None:
            ddl.append(f'        _F(GROUP_NO="{s}", {", ".join(v)}),')
        else:
            oblique.append(f'        _F(GROUP_NO="{s}", ANGL_NAUT={angles}, {", ".join(v)}),')
    return _charge(name, ddl, oblique)
