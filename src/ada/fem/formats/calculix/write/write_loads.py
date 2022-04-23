from typing import TYPE_CHECKING, List

from ada.fem import Load, LoadGravity

if TYPE_CHECKING:
    from ada import FEM


def load_str(load: Load):
    if isinstance(load, LoadGravity):
        return write_gravity_load_str(load)
    else:
        raise ValueError("Calculix does not accept Loads without reference to a fem_set")


def write_gravity_load_str(load: LoadGravity):
    dof = [0, 0, 1] if load.dof is None else load.dof
    fem_set = load.fem_set.name
    return f"""** Name: gravity   Type: Gravity
*Dload
{fem_set}, GRAV, {load.magnitude}, {', '.join([str(x) for x in dof[:3]])}"""


def check_if_grav_loads(fem: "FEM"):
    if LoadGravity in [type(step) for step in fem.steps]:
        return True
    else:
        return False


def get_all_grav_loads(fem: "FEM") -> List[LoadGravity]:
    return list(filter(lambda x: isinstance(x, LoadGravity), fem.get_all_loads()))
