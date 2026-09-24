from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import Constraint, FemSet, Surface

from ..grammar import format_number, format_value, render_keyword
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
        elset = get_instance_name(constraint.s_set, on_assembly_level)
        # The name has nowhere else to go: *Rigid Body takes none, so CAE writes it as a comment.
        return f"** Constraint: {constraint.name}\n*Rigid Body, ref node={rnode}, elset={elset}"
    elif constraint.type == Constraint.TYPES.MPC:
        return _mpc(constraint, on_assembly_level)
    elif constraint.type == Constraint.TYPES.SHELL2SOLID:
        return _shell2solid(constraint, on_assembly_level)
    elif constraint.type == Constraint.TYPES.EQUATION:
        return _equation(constraint, on_assembly_level)
    else:
        raise NotImplementedError(f"{constraint.type}")


def _equation(constraint: Constraint, on_assembly_level: bool) -> str:
    """``*Equation``: the number of terms, then ``node or set, dof, coefficient`` four terms to a
    line, in the constraint's own order -- the first term is the DOF Abaqus eliminates."""
    terms = constraint.equation_terms or ()
    fields = [
        f"{get_instance_name(ref, on_assembly_level)}, {int(dof)}, {format_number(float(coef))}"
        for ref, dof, coef in terms
    ]
    lines = [str(len(terms))] + [", ".join(fields[i : i + 4]) for i in range(0, len(fields), 4)]
    return render_keyword("Equation", (), lines, [f"Constraint: {constraint.name}"]).rstrip()


def _coupling(constraint: Constraint, on_assembly_level: bool):
    dofs_str = "".join(
        [f" {x[0]}, {x[1]}\n" if not isinstance(x, int) else f" {x}, {x}\n" for x in constraint.dofs]
    ).rstrip()

    if type(constraint.s_set) is FemSet:
        # *Coupling takes a SURFACE; adapy's coupling holds a node set, so a node surface over it
        # is written. Named as the set (surfaces and sets are separate namespaces in Abaqus), so
        # the name comes back -- ``<constraint>_surf`` read back as a different operand.
        surf_name = constraint.s_set.name
        parent = constraint.s_set.parent
        if parent is not None and surf_name in parent.surfaces:
            surf_name = f"{constraint.name}_surf"
        new_surf = surface_str(
            Surface(
                surf_name,
                Surface.TYPES.NODE,
                constraint.s_set,
                1.0,
                parent=constraint.s_set.parent,
            ),
            on_assembly_level,
        )
        surface_ref = surf_name
        add_str = new_surf
    else:
        add_str = "**"
        surface_ref = get_instance_name(constraint.s_set, on_assembly_level)

    if constraint.csys is not None:
        new_csys_str = "\n" + csys_str(constraint.csys, on_assembly_level)
        # The name as *Orientation defines it (upper-casing it made the reference and the
        # definition two different strings to anything that compares names exactly).
        cstr = f", Orientation={format_value(constraint.csys.name)}"
    else:
        cstr = ""
        new_csys_str = ""

    # The reference node's SET when it is a named set of the model (the name then survives), its
    # node id otherwise.
    m_set = constraint.m_set
    named = isinstance(m_set, FemSet) and m_set.parent is not None and m_set.name in m_set.parent.nsets
    rnode = get_instance_name(m_set if named else m_set.members[0], on_assembly_level)
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
        pos_tol_str = f", position tolerance={constraint.pos_tol}"

    coupl_text = "**" + num * "-" + """\n** COUPLING {}\n""".format(constraint.name) + "**" + num * "-" + "\n"
    name = constraint.name

    adjust = constraint.metadata.get("adjust", "no")

    coupl_text += f"""** Constraint: {name}
*Tie, name={name}, adjust={adjust}{pos_tol_str}
{constraint.m_set.name}, {constraint.s_set.name}"""
    return coupl_text
