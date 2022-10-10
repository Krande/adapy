from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem import Bc


def create_bc_str(bc: Bc) -> str:
    from ada.fem.utils import is_parent_of_node_solid

    set_name = bc.fem_set.name
    is_solid = False
    for no in bc.fem_set.members:
        is_solid = is_parent_of_node_solid(no)
        if is_solid:
            break
    dofs = ["DX", "DY", "DZ"]
    if is_solid is False:
        dofs += ["DRX", "DRY", "DRZ"]
    bc_str = ""
    for i, n in enumerate(dofs, start=1):
        if i in bc.dofs:
            bc_str += f"{n}=0, "
    dofs_str = f"""dofs = dict(
    GROUP_NO="{set_name}",
    {bc_str}
)\n"""

    return (
        dofs_str
        + f"""{bc.name} = AFFE_CHAR_MECA(
    MODELE=model, DDL_IMPO=_F(**dofs)
)"""
    )
