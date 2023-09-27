from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import Constraint, FemSet, Surface

from .helper_utils import get_instance_name
from .write_orientations import csys_str
from .write_surfaces import surface_str

if TYPE_CHECKING:
    from ada import FEM

# Coupling definition:
# https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-coupling.htm#simakey-r-coupling


def constraints_str(fem: FEM, written_on_assembly_level: bool):
    if len(fem.constraints.keys()) == 0:
        return "** No Constraints"

    return "\n".join([constraint_str(c, written_on_assembly_level) for c in fem.constraints.values()])


def constraint_str(constraint: Constraint, on_assembly_level: bool):
    if constraint.type == Constraint.TYPES.COUPLING:
        return _coupling(constraint, on_assembly_level)
    elif constraint.type == Constraint.TYPES.TIE:
        return _tie(constraint, on_assembly_level)
    elif constraint.type == Constraint.TYPES.RIGID_BODY:
        rnode = get_instance_name(constraint.m_set, on_assembly_level)
        return f"*Rigid Body, ref node={rnode}, elset={get_instance_name(constraint.s_set, on_assembly_level)}"
    elif constraint.type == Constraint.TYPES.MPC:
        return _mpc(constraint, on_assembly_level)
    elif constraint.type == Constraint.TYPES.SHELL2SOLID:
        return _shell2solid(constraint, on_assembly_level)
    else:
        raise NotImplementedError(f"{constraint.type}")


def _coupling(constraint: Constraint, on_assembly_level: bool):
    dofs_str = "".join(
        [f" {x[0]}, {x[1]}\n" if not isinstance(x, int) else f" {x}, {x}\n" for x in constraint.dofs]
    ).rstrip()

    if type(constraint.s_set) is FemSet:
        new_surf = surface_str(
            Surface(
                f"{constraint.name}_surf",
                Surface.TYPES.NODE,
                constraint.s_set,
                1.0,
                parent=constraint.s_set.parent,
            ),
            on_assembly_level,
        )
        surface_ref = f"{constraint.name}_surf"
        add_str = new_surf
    else:
        add_str = "**"
        surface_ref = get_instance_name(constraint.s_set, on_assembly_level)

    if constraint.csys is not None:
        new_csys_str = "\n" + csys_str(constraint.csys, on_assembly_level)
        cstr = f", Orientation={constraint.csys.name.upper()}"
    else:
        cstr = ""
        new_csys_str = ""

    rnode = f"{get_instance_name(constraint.m_set.members[0], on_assembly_level)}"
    return f"""** ----------------------------------------------------------------
** Coupling element {constraint.name}
** ----------------------------------------------------------------{new_csys_str}
** COUPLING {constraint.name}
{add_str}
*COUPLING, CONSTRAINT NAME={constraint.name}, REF NODE={rnode}, SURFACE={surface_ref}{cstr}
*KINEMATIC
{dofs_str}""".rstrip()


def _mpc(constraint, on_assembly_level: bool):
    mpc_type = constraint.mpc_type
    m_members = constraint.m_set.members
    s_members = constraint.s_set.members
    mpc_vars = "\n".join(
        [
            f" {mpc_type},{get_instance_name(m, on_assembly_level):>8},{get_instance_name(s, on_assembly_level):>8}"
            for m, s in zip(m_members, s_members)
        ]
    )
    return f"** Constraint: {constraint.name}\n*MPC\n{mpc_vars}"


def _shell2solid(constraint, on_assembly_level: bool):
    mname = constraint.m_set.name
    sname = constraint.s_set.name
    influence = constraint.influence_distance
    influence_str = "" if influence is None else f", influence distance={influence}"
    return (
        f"** Constraint: {constraint.name}\n*Shell to Solid Coupling, "
        f"constraint name={constraint.name}{influence_str}\n{mname}, {sname}"
    )


def _tie(constraint: Constraint, on_assembly_level: bool) -> str:
    num = 80
    pos_tol_str = ""
    if constraint.pos_tol is not None:
        pos_tol_str = f", position tolerance={constraint.pos_tol},"

    coupl_text = "**" + num * "-" + """\n** COUPLING {}\n""".format(constraint.name) + "**" + num * "-" + "\n"
    name = constraint.name

    adjust = constraint.metadata.get("adjust", "no")

    coupl_text += f"""** Constraint: {name}
*Tie, name={name}, adjust={adjust}{pos_tol_str}
{constraint.m_set.name}, {constraint.s_set.name}"""
    return coupl_text
