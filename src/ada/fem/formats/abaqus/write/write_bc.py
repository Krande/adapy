from typing import TYPE_CHECKING

from ada.fem import Bc

from ..grammar import format_number
from ..mapping import bc_types
from .helper_utils import get_instance_name

if TYPE_CHECKING:
    from ada import Assembly


def abaqus_bc_type(bc_type: str) -> str:
    """The name CAE gives ``bc_type`` in the comment above ``*Boundary`` -- from the one table the
    reader also reads it back through (:func:`..mapping.bc_types`)."""
    return bc_types().to_abaqus(bc_type)


def boundary_conditions_str(assembly: "Assembly"):
    return "\n".join([bc_str(bc, True) for bc in assembly.fem.get_all_bcs()])


def bc_str(bc: "Bc", written_on_assembly_level: bool) -> str:
    ampl_ref_str = ""
    if bc.amplitude is not None:
        ampl_ref_str = ", amplitude=" + bc.amplitude.name

    fem_set = bc.fem_set
    inst_name = get_instance_name(fem_set, written_on_assembly_level)

    aba_type = abaqus_bc_type(bc.type)

    dofs_str = ""
    for dof, magn in zip(bc.dofs, bc.magnitudes):
        if dof is None:
            continue
        magn_str = f", {format_number(magn)}" if magn is not None else ""
        if bc.type in [Bc.TYPES.CONN_DISPL, Bc.TYPES.CONN_VEL] or isinstance(dof, str):
            dofs_str += f" {inst_name}, {dof}{magn_str}\n"
        else:
            dofs_str += f" {inst_name}, {dof}, {dof}{magn_str}\n"

    dofs_str = dofs_str.rstrip()
    add_map = {
        Bc.TYPES.CONN_DISPL: ("*Connector Motion", ", type=DISPLACEMENT"),
        Bc.TYPES.CONN_VEL: ("*Connector Motion", ", type=VELOCITY"),
    }

    if bc.type in add_map.keys():
        bcstr, add_str = add_map[bc.type]
    else:
        bcstr, add_str = "*Boundary", ""

    return f"""** Name: {bc.name} Type: {aba_type}
{bcstr}{ampl_ref_str}{add_str}
{dofs_str}"""
