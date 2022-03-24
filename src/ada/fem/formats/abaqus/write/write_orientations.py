from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from .helper_utils import get_instance_name

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Csys


def orientations_str(fem: FEM, written_on_assembly_level: bool) -> str:
    """Add orientations associated with loads"""
    cstr = "** Orientations associated with Loads"
    for step in fem.steps:
        for load in step.loads:
            if load.csys is None:
                continue
            cstr += "\n"
            coord_str = ", ".join([str(x) for x in chain.from_iterable(load.csys.coords)])[:-1]
            name = load.fem_set.name.upper()
            inst_name = get_instance_name(load.fem_set, written_on_assembly_level)
            cstr += f"*Nset, nset=_T-{name}, internal\n{inst_name},\n"
            cstr += f"*Transform, nset=_T-{name}\n{coord_str}\n"
            cstr += csys_str(load.csys, written_on_assembly_level)

    return cstr.strip()


def csys_str(csys: Csys, written_on_assembly_level: bool):
    """"""
    name = csys.name

    def f(num: float) -> str:
        return f"{num:.3f}"

    ori_str = f'*Orientation, name="{name}"'
    if csys.nodes is None and csys.coords is None:
        ori_str += "\n 1.,           0.,           0.,           0.,           1.,           0.\n 1, 0."
    elif csys.nodes is not None:
        if len(csys.nodes) != 3:
            raise ValueError("CSYS number of nodes must be 3")
        ori_str += ", DEFINITION=NODES, SYSTEM=RECTANGULAR\n {},{},{}".format(
            *[get_instance_name(no, written_on_assembly_level) for no in csys.nodes]
        )
    else:
        ax, ay, az = csys.coords[0]
        ori_str += f" \n {f(ax)}, {f(ay)}, {f(az)}"
        bx, by, bz = csys.coords[1]
        ori_str += f", {f(bx)}, {f(by)}, {f(bz)}"
        if len(csys.coords) == 3:
            cx, cy, cz = csys.coords[2]
            ori_str += f", {f(cx)}, {f(cy)}, {f(cz)}"
        ori_str += "\n 1, 0."
    return ori_str
