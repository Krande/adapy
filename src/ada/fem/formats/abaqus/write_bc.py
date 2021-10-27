from ada.fem import Bc

from .common import get_instance_name

aba_bc_map = dict(
    displacement="Displacement/Rotation",
    velocity="Velocity/Angular velocity",
    connector_displacement="Connector displacement",
    connector_velocity="Connector velocity",
)


valid_aba_bcs = list(aba_bc_map.values()) + [
    "symmetry/antisymmetry/encastre",
    "displacement/rotation",
    "velocity/angular velocity",
]


def bc_str(bc: Bc, written_on_assembly_level: bool) -> str:
    ampl_ref_str = "" if bc.amplitude_name is None else ", amplitude=" + bc.amplitude_name
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
        if bc.type in [Bc.TYPES.CONN_DISPL, Bc.TYPES.CONN_VEL] or type(dof) is str:
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
