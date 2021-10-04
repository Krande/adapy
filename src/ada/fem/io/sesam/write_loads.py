import logging

from ada import FEM
from ada.fem.loads import Load

from .write_utils import write_ff


def loads_str(fem: FEM) -> str:
    loads = fem.steps[0].loads if len(fem.steps) > 0 else []
    out_str = ""
    for i, l in enumerate(loads):
        lid = i + 1
        out_str += write_ff("TDLOAD", [(4, lid, 100 + len(l.name), 0), (l.name,)])
        if l.type in [Load.TYPES.ACC, Load.TYPES.GRAVITY]:
            out_str += load_gravity(l, lid)
        elif l.type == Load.TYPES.FORCE:
            out_str += load_force(l, lid)
        else:
            logging.error(f'Unsupported Load type "{l.type}"')
    return out_str


def load_gravity(load: Load, load_id: int) -> str:
    return write_ff(
        "BGRAV",
        [(load_id, 0, 0, 0), tuple([x * load.magnitude for x in load.acc_vector])],
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
