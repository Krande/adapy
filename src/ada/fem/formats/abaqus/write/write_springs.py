from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Spring


def springs_str(fem: FEM):
    return "\n".join([spring_str(c) for c in fem.springs.values()]) if len(fem.springs) > 0 else "** No Springs"


def spring_str(spring: Spring) -> str:
    from ada.fem.shapes.definitions import SpringTypes

    if spring.type not in (SpringTypes.SPRING1,):
        raise ValueError(f'Currently unsupported spring type "{spring.type}"')

    _str = f'** Spring El "{spring.name}"\n\n'
    for dof, row in enumerate(spring.stiff):
        for j, stiffness in enumerate(row):
            if dof == j:
                _str += f"""*Spring, elset={spring.fem_set.name}
 {dof + 1}
 {stiffness:.6E}
{spring.id}, {spring.nodes[0].id}\n"""
    return _str.rstrip()
