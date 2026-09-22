"""The BNBCD block: boundary conditions, BLDEP companion codes, and retained DOFs.

FIX code 4 is special. "The nodes (degrees of freedom) with FIX = 4 are called
supernodes" (manual printed 6-30), so a DOF carrying it is declared part of the
superelement's *external interface* — the DOFs that survive reduction and that Presel
matches against the assembly. It says nothing about the DOF being constrained, which is
why retained DOFs are not modelled as an ada ``Bc``: no other format writer can then
emit them as a clamped support by mistake. Retained DOFs are Sesam-only.

Two ways to declare them, in this order of precedence:

1. **Explicit** — ``to_fem(..., metadata={"sesam_retained_dofs": {"<nset>": [1, ..., 6]}})``,
   see :data:`RETAINED_KEY` and :func:`retained_dofs_from_metadata`.
2. **Convention** — a node set named :data:`SUPERNODE_SET_NAME`
   (``"SESAM_SUPERNODES"``, matched case-insensitively) on the part FEM or the assembly
   FEM gets all six DOFs retained. This is what makes a plain "read an Abaqus INP, write
   a Sesam FEM" conversion need no extra arguments: name the interface set in the INP and
   it comes out as the superelement interface.

The explicit key *overrides* the convention rather than merging with it: a caller who
names one set must not silently get another set retained too. So passing the key at all —
even as an empty dict, which retains nothing — disables the convention. Passing no key,
or no metadata, falls through to the convention; if neither applies, no DOF gets code 4
and nothing is logged. :func:`retained_dofs` is the entry point that applies that rule.

Everything else in here is the BLDEP companion codes; see :func:`bnbcd_str`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Sequence

from ada.config import logger

from .write_utils import write_ff

if TYPE_CHECKING:
    from ada import FEM

    from .write_constraints import BldepRecord
    from .writer import NodeDofs

# BNBCD FIX codes (manual printed 6-30). "The nodes (degrees of freedom) with FIX = 4
# are called supernodes", which is why code 4 must never be handed out by accident:
# every DOF carrying it becomes part of the superelement's external interface.
FREE = 0
FIXED = 1
PRESCRIBED = 2  # never written: ada's Bc magnitudes are not carried into BNDISPL yet
DEPENDENT = 3
RETAINED = 4

#: ``to_fem(..., metadata={RETAINED_KEY: {"<nset name>": [1, 2, 3, 4, 5, 6]}})``
RETAINED_KEY = "sesam_retained_dofs"

#: Reserved node set name. A node set of this name (matched case-insensitively, like
#: every other set name here) is retained on all six DOFs when no :data:`RETAINED_KEY`
#: metadata is given, so a bare INP -> FEM conversion needs no extra argument.
#:
#: SESAM, not SESTRA: Sesam is the suite and the name of this interface-file format,
#: while Sestra is the solver that consumes it. Models carrying the older spelling are
#: being renamed to match.
SUPERNODE_SET_NAME = "SESAM_SUPERNODES"

#: All six DOFs — what the convention retains on a node that has six, an interface node
#: having no reason to drop any of them. See :func:`_dofs_the_node_has` for the three-DOF
#: case.
ALL_DOFS = (1, 2, 3, 4, 5, 6)


def _dofs_the_node_has(node_id: int, ndofs=None) -> tuple[int, ...]:
    """``(1..ndof)`` for that node — all six unless it is known to carry fewer.

    A solid-only node has three DOFs (manual printed 5-91), and retaining a DOF it does
    not have is not a stricter request, it is an invalid record.
    """
    if ndofs is None:
        return ALL_DOFS
    return tuple(range(1, ndofs.ndof(node_id) + 1))


def retained_dofs(fems: Sequence[FEM], metadata: dict | None = None, ndofs=None) -> dict[int, tuple[int, ...]]:
    """``{node id: dofs}`` to write with FIX code 4 — the writer's single entry point.

    Precedence, as documented at the top of this module: an explicit
    ``metadata["sesam_retained_dofs"]`` wins outright and the convention is not consulted
    (no merging — naming one set must not silently retain another as well). With the key
    absent or ``None``, a node set named :data:`SUPERNODE_SET_NAME` is retained on every
    DOF that node actually has. With neither, ``{}``: no code 4, and nothing logged.

    ``ndofs`` is the per-node DOF count (``writer.node_dofs``), and it clamps the
    *convention* only. "All of them" is the writer's own inference, so on a solid-only
    node — three DOFs, no rotations, manual printed 5-91 — it has to mean 1-3. Without
    the clamp the convention hands out DOFs 4-6 and ``bnbcd_str`` then rejects the
    writer's own output. An explicit metadata request is never clamped: a caller naming a
    DOF the node does not have has asked for something impossible, and gets the error.
    """
    if metadata is not None and metadata.get(RETAINED_KEY) is not None:
        return retained_dofs_from_metadata(fems, metadata)
    return retained_dofs_from_convention(fems, ndofs)


def retained_dofs_from_convention(fems: Sequence[FEM], ndofs=None) -> dict[int, tuple[int, ...]]:
    """Every DOF of every member of a node set named :data:`SUPERNODE_SET_NAME`.

    ``{}`` when no such node set exists — the convention is an offer, not a requirement,
    so a model without the set is not an error. The first of ``fems`` that has the set
    wins, matching how an explicit name resolves.

    Firing is logged at info level with the set name and the node count: a conversion that
    retained nothing and one that retained 58 nodes must be distinguishable in a log.
    """
    for fem in fems:
        if fem is None:
            continue
        try:
            fem_set = fem.sets.get_nset_from_name(SUPERNODE_SET_NAME)
        except ValueError:
            continue
        if len(fem_set.members) == 0:
            logger.warning(
                "sesam writer: node set %s exists but is empty, so no supernodes are written. "
                "FIX code 4 marks the superelement's external interface.",
                fem_set.name,
            )
            return {}
        logger.info(
            "sesam writer: node set %s retains all 6 dofs of its %s nodes as supernodes "
            "(BNBCD FIX code 4, the superelement's external interface). Pass "
            'metadata["%s"] to override this convention.',
            fem_set.name,
            len(fem_set.members),
            RETAINED_KEY,
        )
        return {mem.id: _dofs_the_node_has(mem.id, ndofs) for mem in fem_set.members}

    for fem in fems:
        if fem is None:
            continue
        for elset_name in fem.sets.elements.keys():
            if elset_name.lower() == SUPERNODE_SET_NAME.lower():
                logger.warning(
                    "sesam writer: %s is an element set, not a node set, so no supernodes are "
                    "written. Retained dofs are nodal.",
                    elset_name,
                )
                return {}

    return {}


def retained_dofs_from_metadata(fems: Sequence[FEM], metadata: dict | None) -> dict[int, tuple[int, ...]]:
    """Resolve ``metadata["sesam_retained_dofs"]`` into ``{node id: dofs}``.

    The declared value is ``{nset name: [dofs]}`` with dofs in 1..6. Names are resolved
    through ``fem.sets.get_nset_from_name`` (case-insensitive) against each FEM in
    order, so a set living on the part FEM or on the assembly FEM both work.

    Retained DOFs are a superelement-only concept — they say which DOFs survive
    reduction, not that anything is constrained — so they are declared as Sesam writer
    metadata rather than as a ``Bc``: no other format writer can then emit them as an
    ordinary boundary condition by mistake.

    This is the explicit form, and it is exact: it never adds the
    :data:`SUPERNODE_SET_NAME` convention on top. Call :func:`retained_dofs` for the
    precedence rule; an empty dict here is a deliberate "retain nothing".

    Anything malformed raises ``ValueError``: an unknown set name, a name that is an
    element set, an empty set, no DOFs, or a DOF outside 1..6.
    """
    if not metadata:
        return {}

    spec = metadata.get(RETAINED_KEY)
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise ValueError(
            f'metadata["{RETAINED_KEY}"] must be a dict of {{node set name: [dofs]}}, got {type(spec).__name__}'
        )

    retained: dict[int, tuple[int, ...]] = {}
    for set_name, dofs in spec.items():
        dof_tuple = _validated_dofs(set_name, dofs)
        fem_set = _resolve_nset(fems, set_name)
        if len(fem_set.members) == 0:
            raise ValueError(f'metadata["{RETAINED_KEY}"]: node set "{set_name}" is empty')
        for mem in fem_set.members:
            # The same node named by two sets keeps the union of their DOFs.
            retained[mem.id] = tuple(sorted(set(retained.get(mem.id, ())) | set(dof_tuple)))

    return retained


def _validated_dofs(set_name: str, dofs) -> tuple[int, ...]:
    if isinstance(dofs, (str, bytes)) or not isinstance(dofs, Iterable):
        raise ValueError(
            f'metadata["{RETAINED_KEY}"]: dofs for "{set_name}" must be a list of ints in 1..6, got {dofs!r}'
        )
    dof_tuple = tuple(dofs)
    if not dof_tuple:
        raise ValueError(f'metadata["{RETAINED_KEY}"]: no dofs given for "{set_name}"')
    for dof in dof_tuple:
        if isinstance(dof, bool) or not isinstance(dof, int) or not 1 <= dof <= 6:
            raise ValueError(f'metadata["{RETAINED_KEY}"]: dof {dof!r} for "{set_name}" is not an int in 1..6')
    return tuple(sorted(set(dof_tuple)))


def _resolve_nset(fems: Sequence[FEM], set_name: str):
    """The node set of that name, searched across the given FEMs in order.

    Every FEM's node sets are tried before an element set of the same name is reported:
    ``FemSets`` lets an nset and an elset share a name, and an Abaqus geometry set
    routinely arrives as both an ``*Nset`` and an ``*Elset``. Naming the elset first
    would refuse a model whose node set is sitting right there.
    """
    available: list[str] = []
    for fem in fems:
        if fem is None:
            continue
        available += list(fem.sets.nodes.keys())
        try:
            return fem.sets.get_nset_from_name(set_name)
        except ValueError:
            continue

    for fem in fems:
        if fem is None:
            continue
        for elset_name in fem.sets.elements.keys():
            if elset_name.lower() == set_name.lower():
                raise ValueError(
                    f'metadata["{RETAINED_KEY}"]: "{set_name}" is an element set. Retained dofs are '
                    "nodal, so a node set is required."
                )

    raise ValueError(
        f'metadata["{RETAINED_KEY}"]: node set "{set_name}" is not found. Available node sets: {sorted(available)}'
    )


def bnbcd_str(
    fems: Sequence[FEM],
    lin_deps: Sequence[BldepRecord] = (),
    retained: dict[int, tuple[int, ...]] | None = None,
    ndofs: NodeDofs | None = None,
) -> str:
    """The BNBCD block: one record per node that has anything to say about its DOFs.

    Three sources are merged into the six FIX codes of each node:

    * ``fem.bcs`` -> code 1 on every DOF the boundary condition names. Two BCs touching
      the same node merge into one record holding the union of their DOFs (the writer
      used to emit one record per BC, which left the same node declared twice).
    * ``lin_deps`` (the BLDEP records, see ``write_constraints.BldepRecord``) -> code 3
      on each dependent DOF of the dependent node, and an explicit all-free record for
      each independent (master) node. BLDEP is only valid alongside these codes, manual
      printed 6-27: "The degrees of freedom must also be specified on BNBCD-records as
      linear dependent (3) for the dependent node, and as retained (4) for the
      independent node."

      A master is nevertheless written as free, not retained. Code 4 makes a node a
      supernode, i.e. part of the superelement's external interface, and DNV's own
      GeniE (V9.2-01) writes every one of its BLDEP masters as ``0 0 0 0 0 0`` and
      reserves code 4 for the assembly interface alone. Marking masters retained turned
      743 internal coupling nodes of this project's model into unconnected supernodes in
      Presel, which is the defect this block exists to fix.
    * ``retained`` (``{node id: dofs}`` from :func:`retained_dofs`, i.e. the explicit
      metadata or the :data:`SUPERNODE_SET_NAME` convention) -> code 4, the genuine
      interface. This argument is the only source of code 4; nothing here infers one.

    A real conflict raises ``ValueError`` rather than silently winning: a DOF cannot be
    both fixed and linearly dependent, and a fixed or dependent DOF cannot be retained.

    ``ndofs`` (:class:`writer.NodeDofs`) decides how many FIX codes each record carries.
    BNBCD declares an NDOF of its own and then lists exactly that many codes, so a 3-DOF
    node -- one touched only by solid elements -- gets ``3`` and three codes, not ``6``
    and six. Naming DOF 4, 5 or 6 of such a node is not something to paper over with a
    truncated record: it raises. Left at ``None`` every node is taken to have 6 DOFs,
    which is what this function did before per-node NDOF existed; ``to_fem`` always
    passes the real thing.
    """
    from .writer import ALL_SIX_DOF

    if ndofs is None:
        ndofs = ALL_SIX_DOF

    codes: dict[int, list[int]] = {}

    def node_codes(nid: int) -> list[int]:
        return codes.setdefault(int(nid), [FREE] * 6)

    def check_dof(nid: int, dof: int, what: str) -> None:
        ndof = ndofs.ndof(nid)
        if dof > ndof:
            raise ValueError(
                f"sesam writer: {what} names dof {dof} of node {nid}, which has {ndof} dofs "
                f"(NDOF={ndof}). A node attached only to solid elements has no rotational dofs, so "
                "there is nothing there to constrain, retain or depend on."
            )

    for fem in fems:
        if fem is None:
            continue
        for bc in fem.bcs:
            if bc.fem_set.type != "nset":
                logger.warning(
                    "sesam writer: boundary condition %s is applied to element set %s. BNBCD is "
                    "nodal, so it is skipped.",
                    bc.name,
                    bc.fem_set.name,
                )
                continue
            for mem in bc.fem_set.members:
                node = node_codes(mem.id)
                for dof in range(1, 7):
                    # As before: a named DOF is written as fixed regardless of its
                    # magnitude. Prescribed values (code 2) are not emitted by ada.
                    if dof in bc.dofs:
                        check_dof(mem.id, dof, f'boundary condition "{bc.name}"')
                        node[dof - 1] = FIXED

    chained_master_warned = False
    for record in lin_deps:
        slave = node_codes(record.slave)
        for dof in record.slave_dofs:
            check_dof(record.slave, dof, f"BLDEP dependent node {record.slave} (master {record.master})")
            current = slave[dof - 1]
            if current in (FIXED, PRESCRIBED):
                raise ValueError(
                    f"sesam writer: node {record.slave} dof {dof} is fixed and linearly dependent "
                    f"(BLDEP master {record.master}). A dof cannot be both."
                )
            slave[dof - 1] = DEPENDENT

    for record in lin_deps:
        # The master gets an explicit record even when every code stays 0: GeniE lists
        # its masters, and it documents them as free rather than leaving a reader to
        # infer it from the absence of a record.
        master = node_codes(record.master)
        for dof in record.master_dofs:
            check_dof(record.master, dof, f"BLDEP master node {record.master} (dependent {record.slave})")
        if not chained_master_warned and any(master[dof - 1] == DEPENDENT for dof in record.master_dofs):
            logger.warning(
                "sesam writer: node %s is both a BLDEP master and linearly dependent itself. "
                "Chained dependencies are written as-is; check that Sestra resolves them.",
                record.master,
            )
            chained_master_warned = True

    for nid, dofs in (retained or {}).items():
        node = node_codes(nid)
        for dof in dofs:
            check_dof(nid, dof, "the retained (supernode) set")
            current = node[dof - 1]
            if current == DEPENDENT:
                raise ValueError(
                    f"sesam writer: node {nid} dof {dof} is linearly dependent (BLDEP) and cannot be "
                    f'retained. Remove it from the retained set (metadata["{RETAINED_KEY}"] or the '
                    f"{SUPERNODE_SET_NAME} node set) or from the constraint."
                )
            if current in (FIXED, PRESCRIBED):
                raise ValueError(
                    f"sesam writer: node {nid} dof {dof} is fixed and cannot be retained. A supernode "
                    "dof is kept for the assembly to connect to, not constrained here."
                )
            node[dof - 1] = RETAINED

    out_str = ""
    for nid in sorted(codes):
        c = codes[nid]
        ndof = ndofs.ndof(nid)
        # NDOF codes, no more: a 3-dof node's record is "3" followed by three codes.
        # Padding it out to six would declare rotations the node does not have, which is
        # exactly the inconsistency GNODE's NDOF is being fixed for.
        out_str += write_ff("BNBCD", [(nid, ndof, c[0], c[1]), tuple(c[2:ndof])])
    return out_str
