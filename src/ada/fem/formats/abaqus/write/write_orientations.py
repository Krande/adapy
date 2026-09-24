from __future__ import annotations

from typing import TYPE_CHECKING

from ..grammar import format_number, render_keyword
from .helper_utils import get_instance_name, render_block

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
            # The two points the Keywords Guide's *Transform takes -- a on the local x-axis, b in
            # the local x-y plane -- exactly. This wrote all nine coordinates as text and cut the
            # last CHARACTER off, and then the same system again as an *Orientation.
            a, b = load.csys.coords[0], load.csys.coords[1]
            coord_str = ", ".join(format_number(x) for x in (*a, *b))
            name = load.fem_set.name.upper()
            inst_name = get_instance_name(load.fem_set, written_on_assembly_level)
            cstr += render_keyword("Nset", [("nset", f"_T-{name}"), ("internal", None)], [f"{inst_name},"])
            cstr += render_block("Transform", [("nset", f"_T-{name}")], [coord_str], [f"Transform: {load.csys.name}"])

    return cstr.strip()


def csys_str(csys: Csys, written_on_assembly_level: bool):
    """``*Orientation``, the name always quoted. Coordinates exactly: ``{:.3f}`` rounded them to
    a millimetre in metres."""
    params = [("name", f'"{csys.name}"')]
    if csys.nodes is None and csys.coords is None:
        data = [" 1.,           0.,           0.,           0.,           1.,           0.", " 1, 0."]
    elif csys.nodes is not None:
        if len(csys.nodes) != 3:
            raise ValueError("CSYS number of nodes must be 3")
        params += [("DEFINITION", "NODES"), ("SYSTEM", "RECTANGULAR")]
        data = [" {},{},{}".format(*[get_instance_name(no, written_on_assembly_level) for no in csys.nodes])]
    else:
        points = csys.coords[:3] if len(csys.coords) == 3 else csys.coords[:2]
        data = [" " + ", ".join(format_number(x) for point in points for x in point), " 1, 0."]
    return render_block("Orientation", params, data)
