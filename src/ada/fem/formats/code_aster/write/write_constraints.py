"""Couplings for Code_Aster.

A coupling (or rigid body) ties its dependent nodes rigidly to a reference node, written as ``LIAISON_SOLIDE``
over the reference node set and the dependent node set. A reference node that belongs to no element has no
dofs in Code_Aster, so each one gets a ``POI1`` cell with a zero-stiffness ``DIS_TR`` discrete element. That
gives it the 6 dofs a support or a load can act on, without adding stiffness or mass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.fem import Constraint

from .names import concept_name

if TYPE_CHECKING:
    from ada.api.spatial import Part

#: The element group the reference points of all couplings are created in (:func:`ref_points_group`)
REF_POINTS_GROUP = "ref_pts"


def get_couplings(part: Part) -> list[Constraint]:
    couplings = []
    for fem in _fems(part):
        for con in fem.constraints.values():
            if con.type in (Constraint.TYPES.TIE, Constraint.TYPES.EQUATION, Constraint.TYPES.SHELL2SOLID):
                continue  # get_links
            if con.type not in (Constraint.TYPES.COUPLING, Constraint.TYPES.RIGID_BODY):
                from ada.fem.formats import conversion_report

                # reported, not logged: a constraint left out changes the model without a word
                conversion_report.current().omitted(
                    "code_aster writer",
                    "Constraint",
                    con.name,
                    f'a "{con.type}" constraint has no Code_Aster form here',
                )
                continue
            if con not in couplings:
                couplings.append(con)
    return couplings


def get_links(part: Part) -> list[Constraint]:
    """The ties and shell-to-solid couplings (``LIAISON_MAIL``) and equations (``LIAISON_DDL``) of the model, each once
    (:func:`create_link_str`)."""
    kinds = (Constraint.TYPES.TIE, Constraint.TYPES.EQUATION, Constraint.TYPES.SHELL2SOLID)
    out = []
    for fem in _fems(part):
        for con in fem.constraints.values():
            if con.type in kinds and con not in out:
                out.append(con)
    return out


def _equation_rows(con: Constraint) -> list[tuple[list, list, list]] | None:
    """``[(nodes, dofs, coefficients)]``, one per equation: a term naming a node set stands for each of its nodes in
    turn (the i-th equation takes the i-th node of every set, as in Abaqus), so the sets must be equally long."""
    from ada.fem import FemSet

    terms = con.equation_terms or ()
    columns = [list(ref.members) if isinstance(ref, FemSet) else [ref] for ref, _, _ in terms]
    lengths = {len(c) for c in columns if len(c) != 1}
    if len(terms) < 2 or len(lengths) > 1:
        return None
    n_eq = lengths.pop() if lengths else 1
    return [
        (
            [c[i] if len(c) > 1 else c[0] for c in columns],
            [int(dof) for _, dof, _ in terms],
            [float(coef) for _, _, coef in terms],
        )
        for i in range(n_eq)
    ]


def _tie_main_groups(con: Constraint) -> tuple[list[str], str | None]:
    """The element groups of a tie's main surface and its kind (``"MASSIF"``, ``"COQUE"``), or ``([], why)``."""
    from ada.fem import FemSet
    from ada.fem.shapes.definitions import ShellShapes, SolidShapes
    from ada.fem.surfaces import Surface

    m = con.m_set
    if isinstance(m, Surface):
        if m.type != Surface.TYPES.ELEMENT:
            return [], "its main surface is a node surface; LIAISON_MAIL projects onto cells"
        sets = list(m.fem_set) if isinstance(m.fem_set, (list, tuple)) else [m.fem_set]
    elif isinstance(m, FemSet) and m.type == FemSet.TYPES.ELSET:
        sets = [m]
    else:
        return [], "its main side is a node set; LIAISON_MAIL projects onto cells"
    types = {type(getattr(el, "type", None)) for fs in sets for el in fs.members}
    if types == {SolidShapes}:
        return [fs.name for fs in sets], "MASSIF"
    if types == {ShellShapes}:
        return [fs.name for fs in sets], "COQUE"
    return [], "its main surface mixes solids and shells, or is neither (COQUE_MASSIF needs the normals and thickness)"


def model_bcs(part: Part) -> list:
    """The boundary conditions of the part's FEM and of its assembly's, each once; one in a rotated frame that is
    global axes in another order as the global one it is (:func:`ada.fem.frames.in_global_axes`)."""
    out = []
    for fem in _fems(part):
        out += [bc for bc in fem.bcs if all(bc is not b for b in out)]
    return _in_global_axes(out)


def _in_global_axes(bcs: list) -> list:
    from ada.fem.frames import in_global_axes

    return [in_global_axes(bc) or bc for bc in bcs]


def step_bcs(part: Part, step) -> list:
    """The boundary conditions in force in ``step``: the model's (:func:`model_bcs`), then the own ``Bc``s of every
    step from the first through ``step``, in deck order (:func:`.write_steps.all_steps`).

    A ``Bc`` added to a step (``Step.add_bc``; where the Abaqus reader puts a ``*Boundary`` found inside a ``*Step``)
    holds in that step and in every later one, as in Abaqus and in CalculiX, whose ``*BOUNDARY`` in a step carries
    into the next (the CalculiX writer writes it in its step, :func:`ada.fem.formats.calculix.write.write_steps._bcs_str`).
    Code_Aster solves each step from its own ``EXCIT`` list, so a step lists them again. They were left out of the
    deck without a word: a cantilever held in dx, dy, dz by the model and in rx, ry, rz by its step stopped at
    <FACTOR_11> (a mechanism), and with any other support holding the rotations it would have been solved wrongly.
    ``step`` None: the model's only."""
    from .write_steps import all_steps

    out = model_bcs(part)
    if step is None:
        return out
    for st in all_steps(part.get_assembly()):
        out += _in_global_axes([bc for bc in st.bcs.values() if all(bc is not b for b in out)])
        if st is step:
            break
    return out


@dataclass
class StepCharges:
    """The supports a step is solved with."""

    #: Every charge of the supports: the one of all supports, the one holding the prescribed dofs at zero (a static
    #: step giving them values replaces it with its own) and the couplings.
    names: list[str]
    #: The name among ``names`` of the charge holding the prescribed dofs at zero; None for none.
    zero: str | None
    #: The ``AFFE_CHAR_MECA`` of the step's own charges, to go before the step's solve; empty when the step is solved
    #: with the model's (written once, by the writer).
    definitions: str
    #: The boundary conditions in force (:func:`step_bcs`).
    bcs: list


def step_charges(part: Part, step=None) -> StepCharges:
    """The charges ``step`` is solved with (``None``: the model's).

    A step with no ``Bc`` of its own in force (:func:`step_bcs`) uses the model's :data:`.write_bc.SUPPORTS` and
    :data:`.write_bc.PRESCRIBED_AT_ZERO`. One with them gets its own two, named after its position in the deck
    (``supports_2``, ``prescribed_zero_2``), holding the model's supports and the steps' together: one charge of all
    supports, as a dof held by two charges stops Code_Aster (<ASSEMBLA_26>, see :mod:`.write_bc`).
    """
    from ada.fem.formats.prescribed import prescribed_dofs

    from .write_bc import (
        PRESCRIBED_AT_ZERO,
        SUPPORTS,
        prescribed_at_zero_str,
        prescribed_charge_str,
        supports_str,
    )
    from .write_steps import all_steps

    bcs = step_bcs(part, step)
    prescribed = prescribed_dofs(bcs)
    own = len(bcs) > len(model_bcs(part))
    sup_name, zero_name = SUPPORTS, PRESCRIBED_AT_ZERO
    sup, zero = supports_str(bcs, prescribed), prescribed_at_zero_str(bcs)
    definitions = []
    if own:
        k = next(i for i, st in enumerate(all_steps(part.get_assembly()), start=1) if st is step)
        sup_name, zero_name = f"{SUPPORTS}_{k}", f"{PRESCRIBED_AT_ZERO}_{k}"
        if sup:
            definitions.append(supports_str(bcs, prescribed, sup_name))
        if zero:
            definitions.append(prescribed_charge_str(zero_name, {key: 0.0 for key in prescribed}, bcs))
    names = []
    if sup:
        names.append(sup_name)
    if zero:
        names.append(zero_name)
    names += [concept_name(con, "coupling") for con in get_couplings(part)]
    names += [concept_name(con, "link") for con in get_links(part) if con in _WRITTEN_LINKS]
    return StepCharges(names, zero_name if zero else None, "\n".join(definitions), bcs)


def get_charge_names(part: Part, step=None) -> list[str]:
    """The names of all mechanical loads that make up the supports ``step`` is solved with (:func:`step_charges`)."""
    return step_charges(part, step).names


def has_cara_elem(part: Part) -> bool:
    """Whether the model needs element characteristics (AFFE_CARA_ELEM)"""
    fem = part.fem
    if len(fem.sections.lines) > 0 or len(fem.sections.shells) > 0 or len(get_couplings(part)) > 0:
        return True
    # A mass of any kind and a spring are discrete elements whose matrices are element characteristics (write_masses).
    return len(fem.springs) > 0 or any(
        (m.type == m.TYPES.NONSTRU and m.fem_set is not None) or m.elset is not None for m in fem.elements.masses
    )


def ref_points_group(part: Part) -> str:
    """The element group the couplings' reference points are created in: :data:`REF_POINTS_GROUP`, or
    ``ref_pts_2``, ``ref_pts_3``, ... when the model has an element set of that name.

    ``CREA_POI1`` does not create a group that exists -- measured, Code_Aster 18.1.8: a shell cantilever coupled
    at its root, with a user element set named ``ref_pts``, stopped at <MESH1_20> ("il existe déjà"). A node set
    of that name is no clash (node and element groups are apart; measured: it solved)."""
    taken = {name for fem in _fems(part) for name in fem.elsets.keys()}
    group, n = REF_POINTS_GROUP, 2
    while group in taken:
        group = f"{REF_POINTS_GROUP}_{n}"
        n += 1
    return group


def create_ref_points_mesh_str(
    part: Part, input_mesh: str, output_mesh: str = "mesh_ref", group: str = REF_POINTS_GROUP
) -> str:
    ref_sets = ", ".join(f"'{con.m_set.name}'" for con in get_couplings(part))
    return f"""{output_mesh} = CREA_MAILLAGE(
    MAILLAGE={input_mesh},
    CREA_POI1=_F(NOM_GROUP_MA='{group}', GROUP_NO=({ref_sets},)),
)
"""


def create_ref_points_model_str(group: str = REF_POINTS_GROUP) -> str:
    return f"_F(GROUP_MA='{group}', PHENOMENE='MECANIQUE', MODELISATION='DIS_TR',),"


def create_ref_points_discrete_items(group: str = REF_POINTS_GROUP) -> str:
    """The ``DISCRET`` items of the reference points: zero stiffness and zero mass."""
    return f"""            _F(GROUP_MA='{group}', CARA='K_TR_D_N', VALE=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            _F(GROUP_MA='{group}', CARA='M_TR_D_N', VALE=(0.0,) * 10),"""


def create_ref_points_discrete_str(group: str = REF_POINTS_GROUP) -> str:
    return f"""
        DISCRET=(
{create_ref_points_discrete_items(group)}
        ),"""


#: The ties and equations a deck has an AFFE_CHAR_MECA for (create_link_str wrote one); the steps name only those.
_WRITTEN_LINKS: list = []

#: ``{node id: one-node FemSet}`` of the nodes of the partial couplings, while one deck is written (writer.to_fem).
_LINK_GROUPS: list[dict] = []

_DOF_NAMES = ("DX", "DY", "DZ", "DRX", "DRY", "DRZ")


def is_partial(con: Constraint) -> bool:
    return con.dofs is not None and sorted(int(d) for d in con.dofs) != [1, 2, 3, 4, 5, 6]


def add_link_node_groups(part: Part) -> list:
    """One-node node groups (``lk1``, ``lk2``, ...) of every node of a partial coupling: ``LIAISON_DDL`` names its
    nodes by groups of one. Added to the part's FEM for this deck only (and taken off again by the writer)."""
    from ada.fem import FemSet

    groups: dict[int, FemSet] = {}
    taken = {fs.name for fem in _fems(part) for fs in fem.sets}
    k = 0

    def one_node(node):
        nonlocal k
        if int(node.id) in groups:
            return
        k += 1
        while f"lk{k}" in taken:
            k += 1
        groups[int(node.id)] = part.fem.sets.add(FemSet(f"lk{k}", [node], FemSet.TYPES.NSET))

    for con in get_couplings(part):
        if not is_partial(con):
            continue
        for node in [*con.m_set.members, *con.s_set.members]:
            one_node(node)
    # an equation's nodes (LIAISON_DDL), and a tie's secondary nodes as one group (LIAISON_MAIL's GROUP_NO_ESCL)
    from ada.fem.surfaces import surface_nodes

    t = 0
    from ada.fem.shell_to_solid import edge_joint
    from ada.fem.surfaces import surface_facets

    def new_set(prefix, members, kind):
        nonlocal t
        t += 1
        while f"{prefix}{t}" in taken:
            t += 1
        return part.fem.sets.add(FemSet(f"{prefix}{t}", list(members), kind))

    for con in get_links(part):
        if con.type == Constraint.TYPES.EQUATION:
            for nodes, _, _ in _equation_rows(con) or []:
                for node in nodes:
                    one_node(node)
            continue
        if con.type == Constraint.TYPES.SHELL2SOLID:
            # the edge nodes (eliminated), the solid cells of the face, the shell cells at the edge (their normals)
            joint = edge_joint(con)
            solids = {id(f.element): f.element for f in surface_facets(con.s_set) if f.element is not None}
            if joint.why is None and solids:
                groups[("s2s", con.name)] = (
                    new_set("s2e", joint.nodes, FemSet.TYPES.NSET),
                    new_set("s2m", solids.values(), FemSet.TYPES.ELSET),
                    new_set("s2c", joint.shells, FemSet.TYPES.ELSET),
                    joint.thickness,
                )
            continue
        nodes = surface_nodes(con.s_set)
        if nodes:
            t += 1
            while f"tie{t}" in taken:
                t += 1
            groups[("tie", con.name)] = part.fem.sets.add(FemSet(f"tie{t}", list(nodes), FemSet.TYPES.NSET))
    _LINK_GROUPS.append(groups)
    out = []
    for g in groups.values():
        out += list(g[:3]) if isinstance(g, tuple) else [g]
    return out


def remove_link_node_groups(part: Part, added: list) -> None:
    for fs in added:
        part.fem.sets.remove(fs)
    if _LINK_GROUPS:
        _LINK_GROUPS.pop()


def create_link_str(con: Constraint) -> str:
    """A tie or an equation as its ``AFFE_CHAR_MECA``, or ``""`` when it has none here (reported by name).

    * A tie (Abaqus ``*Tie``; ``m_set`` main, ``s_set`` secondary): ``LIAISON_MAIL`` (U4.44.01 5.13), each secondary
      node held to its projection on the main surface's cells -- the main elements themselves, the solid cells next
      to the face for a solid (``TYPE_RACCORD='MASSIF'``, the translations) or the shells (``'COQUE'``, the six dofs,
      the translations alone with ``no_rotation``); the position tolerance as ``DISTANCE_MAX``. The interpolation
      over the facet is Abaqus's tie term for term, as the Sesam writer's BLDEP is.
    * An equation (``sum A_i u(n_i, d_i) = 0``): one ``LIAISON_DDL`` per node tuple, its nodes by groups of one.
    """
    from ada.fem.formats import conversion_report

    name = concept_name(con, "link")
    groups = _LINK_GROUPS[-1] if _LINK_GROUPS else {}
    rep = conversion_report.current()
    if con.type == Constraint.TYPES.EQUATION:
        rows = _equation_rows(con)
        if not rows:
            rep.omitted(
                "code_aster writer",
                "Constraint",
                con.name,
                "an equation of fewer than two terms, or of node sets that differ in length",
            )
            return ""
        out = []
        for nodes, dofs, coefs in rows:
            out.append(
                f"        _F(GROUP_NO=({', '.join(repr(groups[int(n.id)].name) for n in nodes)}), "
                f"DDL=({', '.join(repr(_DOF_NAMES[d - 1]) for d in dofs)}), "
                f"COEF_MULT=({', '.join(repr(c) for c in coefs)}), COEF_IMPO=0.0),"
            )
        body = "\n".join(out)
        return f"""{name} = AFFE_CHAR_MECA(
    MODELE=model,
    LIAISON_DDL=(
{body}
    ),
)"""
    if con.type == Constraint.TYPES.SHELL2SOLID:
        made = groups.get(("s2s", con.name))
        if made is None:
            from ada.fem.shell_to_solid import edge_joint

            why = edge_joint(con).why or "its solid side has no faces (a node set or node surface)"
            rep.omitted("code_aster writer", "Constraint", con.name, f"a shell-to-solid coupling: {why}")
            return ""
        edge, solid, shells, h = made
        # COQUE_MASSIF (U4.44.01 5.13.3): the edge nodes' six dofs from the solid -- translations at the node, rotations
        # of the shell's normal fibre from the solid at its ends +-h/2 along the shell's normal (ada.fem.shell_to_solid)
        return f"""{name}_nrm = CREA_CHAMP(TYPE_CHAM='NOEU_GEOM_R', OPERATION='NORMALE', MODELE=model, GROUP_MA='{shells.name}')
{name} = AFFE_CHAR_MECA(
    MODELE=model,
    LIAISON_MAIL=_F(
        GROUP_MA_MAIT='{solid.name}', GROUP_NO_ESCL='{edge.name}', TYPE_RACCORD='COQUE_MASSIF', EPAIS={float(h)!r},
        CHAM_NORMALE={name}_nrm,
    ),
)"""
    main, kind = _tie_main_groups(con)
    slave = groups.get(("tie", con.name))
    if not main or slave is None:
        rep.omitted("code_aster writer", "*TIE", con.name, kind if not main else "its secondary surface has no nodes")
        return ""
    extra = ""
    if kind == "COQUE" and bool(con.metadata.get("no_rotation", False)):
        extra += ", DDL=('DX', 'DY', 'DZ')"
    if con.pos_tol is not None:
        extra += f", DISTANCE_MAX={float(con.pos_tol)!r}"
    mait = ", ".join(repr(g) for g in main)
    return f"""{name} = AFFE_CHAR_MECA(
    MODELE=model,
    LIAISON_MAIL=_F(GROUP_MA_MAIT=({mait},), GROUP_NO_ESCL='{slave.name}', TYPE_RACCORD='{kind}'{extra}),
)"""


def create_coupling_str(con: Constraint) -> str:
    """A coupling: ``LIAISON_SOLIDE`` (a rigid link in all six dofs), or -- for one in some dofs only, a GeniE rigid
    link of the translations, or of some of them -- the rigid link's own kinematics in those dofs as one
    ``LIAISON_DDL`` per dependent node and dof: ``u_s = u_m + theta_m x (x_s - x_m)`` for a translation, ``theta_s =
    theta_m`` for a rotation.

    It used to be ``LIAISON_SOLIDE`` whatever the dofs, with a log line: a GeniE rigid link of the translations only
    (BLDEP's 9 terms, the slaves' rotations free) clamped its slaves' rotations too."""
    import numpy as np

    name = concept_name(con, "coupling")
    if not is_partial(con):
        return f"""{name} = AFFE_CHAR_MECA(
    MODELE=model, LIAISON_SOLIDE=_F(GROUP_NO=('{con.m_set.name}', '{con.s_set.name}'))
)"""
    groups = _LINK_GROUPS[-1] if _LINK_GROUPS else {}
    (master,) = con.m_set.members
    xm = np.asarray(master.p, dtype=float)
    mg = groups[int(master.id)].name
    rows = []
    for s in con.s_set.members:
        if int(s.id) == int(master.id):
            continue
        sg = groups[int(s.id)].name
        rx, ry, rz = np.asarray(s.p, dtype=float) - xm
        # u_s,i - u_m,i - (theta_m x r)_i = 0, (theta x r) = (ty rz - tz ry, tz rx - tx rz, tx ry - ty rx)
        terms = {
            1: [("DRY", rz), ("DRZ", -ry)],
            2: [("DRZ", rx), ("DRX", -rz)],
            3: [("DRX", ry), ("DRY", -rx)],
        }
        for d in sorted(int(d) for d in con.dofs):
            ddl = _DOF_NAMES[d - 1]
            nodes, dofs, coefs = [sg, mg], [ddl, ddl], [1.0, -1.0]
            for rot, c in terms.get(d, []):
                if abs(c) > 0.0:
                    nodes.append(mg)
                    dofs.append(rot)
                    coefs.append(-float(c))
            rows.append(
                f"        _F(GROUP_NO=({', '.join(repr(g) for g in nodes)}), DDL=({', '.join(repr(x) for x in dofs)}), "
                f"COEF_MULT=({', '.join(repr(c) for c in coefs)}), COEF_IMPO=0.0),"
            )
    body = "\n".join(rows)
    return f"""{name} = AFFE_CHAR_MECA(
    MODELE=model,
    LIAISON_DDL=(
{body}
    ),
)"""


def _fems(part: Part):
    yield part.fem
    assembly = part.get_assembly()
    if assembly is not part:
        yield assembly.fem
