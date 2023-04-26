from ada import FEM
from ada.config import logger
from ada.fem.loads import Load, LoadGravity

from .write_utils import write_ff


def loads_str(fem: FEM) -> str:
    if len(fem.steps) == 0:
        return ""
    step = fem.steps[0]
    if len(step.loads) == 0:
        return ""

    if len(step.load_cases.keys()) > 0:
        out_str = ""
        for i, lc in enumerate(step.load_cases.values(), start=1):
            out_str += write_ff("TDLOAD", [(4, i, 100 + len(lc.name), 0), (lc.name,)])
            for load in lc.loads:
                out_str += load_str(load, i)
        return out_str

    lid = 1
    load_case_name = "LC1"
    out_str = write_ff("TDLOAD", [(4, lid, 100 + len(load_case_name), 0), (load_case_name,)])
    for load in step.loads:
        out_str += load_str(load, lid)

    return out_str


def load_str(load: Load, lid):
    if load.type in [Load.TYPES.ACC, Load.TYPES.GRAVITY]:
        return load_gravity(load, lid)
    elif load.type == Load.TYPES.FORCE:
        return load_force(load, lid)
    else:
        logger.error(f'Unsupported Load type "{load.type}"')


def load_gravity(load: Load, load_id: int) -> str:
    """Gravity Acceleration field"""
    if isinstance(load, LoadGravity):
        load_vector = tuple([0, 0, load.magnitude])
    else:
        load_vector = tuple(load.acc_vector)
    return write_ff(
        "BGRAV",
        [(load_id, 0, 0, 0), load_vector],
    )


def load_force(load: Load, load_id: int) -> str:
    """Node with load"""
    lotype = 0
    complx = 0  # Assumed no phase shift
    node_no = load.fem_set.members[0].id
    forces = load.forces
    real_loads_1 = tuple([node_no, 6] + forces[:2])
    real_loads_2 = tuple(forces[2:])
    return write_ff(
        "BNLOAD",
        [(load_id, lotype, complx, 0), real_loads_1, real_loads_2],
    )
