from typing import TYPE_CHECKING

from ada.fem import Bc

from .helper_utils import get_instance_name

if TYPE_CHECKING:
    from ada import Assembly


aba_bc_map = {
    Bc.TYPES.DISPL: "Displacement/Rotation",
    Bc.TYPES.VELOCITY: "Velocity/Angular velocity",
    Bc.TYPES.CONN_DISPL: "Connector displacement",
    Bc.TYPES.CONN_VEL: "Connector velocity",
}


valid_aba_bcs = list(aba_bc_map.values()) + [
    "symmetry/antisymmetry/encastre",
    "displacement/rotation",
    "velocity/angular velocity",
]


def boundary_conditions_str(assembly: "Assembly"):
    return "\n".join([bc_str(bc, True) for bc in assembly.fem.get_all_bcs()])


def bc_str(bc: "Bc", written_on_assembly_level: bool) -> str:
    ampl_ref_str = ""
    if bc.amplitude is not None:
        ampl_ref_str = ", amplitude=" + bc.amplitude.name

    fem_set = bc.fem_set
    inst_name = get_instance_name(fem_set, written_on_assembly_level)

    if bc.type in valid_aba_bcs:
        aba_type = bc.type
    else:
        aba_type = aba_bc_map[bc.type]

    dofs_str = ""
    for dof, magn in zip(bc.dofs, bc.magnitudes):
        if dof is None:
            continue
        magn_str = f", {magn:.6E}" if magn is not None else ""
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
