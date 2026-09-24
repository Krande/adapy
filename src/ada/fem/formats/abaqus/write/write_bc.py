from typing import TYPE_CHECKING

from ada.fem import Bc

from ..grammar import format_number
from ..mapping import bc_types
from .helper_utils import get_instance_name, render_block

if TYPE_CHECKING:
    from ada import Assembly


def abaqus_bc_type(bc_type: str) -> str:
    """The name CAE gives ``bc_type`` in the comment above ``*Boundary`` -- from the one table the
    reader also reads it back through (:func:`..mapping.bc_types`)."""
    return bc_types().to_abaqus(bc_type)


def boundary_conditions_str(assembly: "Assembly"):
    return "\n".join([bc_str(bc, True) for bc in assembly.fem.get_all_bcs()])


def bc_str(bc: "Bc", written_on_assembly_level: bool) -> str:
    params = [] if bc.amplitude is None else [("amplitude", bc.amplitude.name)]

    fem_set = bc.fem_set
    inst_name = get_instance_name(fem_set, written_on_assembly_level)

    aba_type = abaqus_bc_type(bc.type)

    lines = []
    for dof, magn in zip(bc.dofs, bc.magnitudes):
        if dof is None:
            continue
        magn_str = f", {format_number(magn)}" if magn is not None else ""
        if bc.type in [Bc.TYPES.CONN_DISPL, Bc.TYPES.CONN_VEL] or isinstance(dof, str):
            lines.append(f" {inst_name}, {dof}{magn_str}")
        else:
            lines.append(f" {inst_name}, {dof}, {dof}{magn_str}")

    add_map = {
        Bc.TYPES.CONN_DISPL: ("Connector Motion", [("type", "DISPLACEMENT")]),
        Bc.TYPES.CONN_VEL: ("Connector Motion", [("type", "VELOCITY")]),
    }
    keyword, add_params = add_map.get(bc.type, ("Boundary", []))
    return render_block(keyword, params + add_params, lines or [""], [f"Name: {bc.name} Type: {aba_type}"])
