from ada.fem import Load, LoadPressure
from ada.fem.exceptions.model_definition import UnsupportedLoadType

from .helper_utils import get_instance_name


def load_str(load: Load) -> str:
    load_map = {
        Load.TYPES.GRAVITY: acceleration_field_str,
        Load.TYPES.ACC: acceleration_field_str,
        Load.TYPES.FORCE: force_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
    }
    load_str_func = load_map.get(load.type, None)

    if load_str_func is None:
        raise ValueError("Unsupported load type", load.type)

    return load_str_func(load)


def acceleration_field_str(load: Load) -> str:
    dof = [0, 0, 1] if load.dof is None else [dof if dof is not None else 0 for dof in load.dof]
    dof_str = ", ".join([str(x) for x in dof[:3]])
    return f"""** Name: gravity   Type: Gravity\n*Dload\n, GRAV, {load.magnitude}, {dof_str}"""


def force_load_str(load: Load) -> str:
    lstr = ""
    bc_text_f = ""
    bc_text_m = ""

    fo = 0
    instance_name = get_instance_name(load.fem_set, True)
    for i, f in enumerate(load.dof[:3]):
        if f == 0.0 or f is None:
            continue
        total_force = f * load.magnitude
        bc_text_f += f" {instance_name}, {i + 1}, {total_force}\n"
        fo += 1

    mom = 0
    for i, m in enumerate(load.dof[3:]):
        if m == 0.0 or m is None:
            continue
        mom += 1
        bc_text_m += f" {instance_name}, {i + 4}, {m}\n"

    lstr += "\n" if "\n" not in lstr[-2:] != "" else ""
    follower_str = "" if load.follower_force is False else ", follower"
    follower_str += f", amplitude={load.amplitude}" if load.amplitude is not None else ""
    if fo != 0:
        forc_name = load.name + "_F"
        lstr += f"** Name: {forc_name}   Type: Concentrated force\n*Cload{follower_str}\n{bc_text_f}"
    if mom != 0:
        mom_name = load.name + "_M"
        lstr += f"** Name: {mom_name}   Type: Moment\n*Cload{follower_str}\n{bc_text_m}"
    return lstr.strip()


def pressure_load_str(load: LoadPressure) -> str:
    instance_name = get_instance_name(load.surface, True)

    if load.distribution == LoadPressure.P_DIST_TYPES.TOTAL_FORCE:
        raise UnsupportedLoadType("Total Force calculation is not yet supported for Abaqus")

    return f"""** Name: {load.name}   Type: Pressure
*Dsload
{instance_name}, P, {load.magnitude}"""
