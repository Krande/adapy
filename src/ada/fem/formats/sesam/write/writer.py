from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.config import logger
from ada.core.utils import Counter, get_current_user
from ada.fem import FEM
from ada.fem.exceptions.model_definition import DoesNotSupportMultiPart
from ada.fem.shapes.definitions import (
    ConnectorTypes,
    LineShapes,
    MassTypes,
    ShellShapes,
    SolidShapes,
    SpringTypes,
)

from .templates import top_level_fem_str
from .write_sets import sets_str
from .write_utils import write_ff

if TYPE_CHECKING:
    from ada import Material


def to_fem(assembly, name, analysis_dir=None, metadata=None, model_data_only=False):
    """Write a Sesam input interface file (``<name>T1.FEM``).

    Sesam-specific ``metadata`` keys:

    * ``"control_file"`` — an existing Sestra control file to reuse.
    * ``"sesam_retained_dofs"`` — ``{node set name: [dofs]}``, dofs being ints in 1..6,
      naming the DOFs to write on BNBCD with FIX code 4, i.e. the nodes that become
      supernodes: the superelement's external interface that Presel matches against the
      assembly. Code 4 says which DOFs survive reduction, not that anything is
      constrained. Set names resolve case-insensitively against the part FEM and then the
      assembly FEM; anything malformed raises ``ValueError``. This metadata and the
      convention below are the *only* sources of code 4 — nothing is inferred from
      constraints, and in particular a BLDEP master node is written as free (see
      ``write_bcs.bnbcd_str``). Sesam-only by design: a retained DOF is a superelement
      reduction concept, not a boundary condition, so it is not a ``Bc`` that other
      format writers would emit as a clamped support.

          assembly.to_fem(name, "sesam", metadata={"sesam_retained_dofs": {"INTERFACE": [1, 2, 3, 4, 5, 6]}})

      With no such key, a node set named ``SESAM_SUPERNODES``
      (``write_bcs.SUPERNODE_SET_NAME``, matched case-insensitively) on the part FEM or
      the assembly FEM has all six DOFs of its members retained, and the writer logs that
      set's name and node count at info level. That convention is what lets a plain "read
      an Abaqus INP, write a Sesam FEM" conversion need no extra arguments: name the
      interface set in the INP and it comes out as the superelement interface. Note the
      spelling — Sesam is the suite and the name of this interface-file format, while
      Sestra is the solver that consumes it.

      The explicit key wins outright and is never merged with the convention: a caller
      who names one set must not silently get another set retained too. Passing the key
      at all disables the convention, so ``metadata={"sesam_retained_dofs": {}}`` is an
      explicit "retain nothing". With neither, no DOF gets code 4 and nothing is logged.

      The round trip closes: the reader collects the code-4 nodes back into a node set
      named ``SESAM_SUPERNODES`` (``read_constraints.add_supernode_set``), which is the
      same name this convention picks up, so Sesam -> ada -> Sesam preserves the
      interface with no caller action. One detail is not preserved — a deck whose nodes
      retain only *some* of their DOFs comes back widened to all of them, with a warning
      naming the affected nodes, because a node set cannot carry a per-DOF pattern.
    """
    from .write_bcs import bnbcd_str, retained_dofs
    from .write_constraints import bldep_records
    from .write_elements import elem_gen
    from .write_loads import loads_str
    from .write_masses import mass_str
    from .write_sections import sections_str
    from .write_steps import write_sestra_inp

    if metadata is None:
        metadata = dict()

    if "control_file" not in metadata.keys():
        metadata["control_file"] = None

    parts = list(filter(lambda x: len(x.fem.nodes) > 0, assembly.get_all_subparts(include_self=True)))
    # Merging a multi-instance model is ``fem.formats.general``'s job, not this writer's: it
    # builds a temporary single-part assembly from a non-destructive merge and hands that
    # here, so by the time a public ``to_fem`` reaches this line there is one part. This used
    # to call ``concatenate_fem_to_single_part(assembly)`` itself and then re-list the
    # assembly's parts — but that merge is non-destructive and returns the merged part, so the
    # re-listing found the same parts again and fell straight into the error below. Dead code
    # with a comment promising multi-part support it did not provide; one merge path is enough.
    if len(parts) != 1:
        raise DoesNotSupportMultiPart(
            f"Sesam writer currently only works for a single part. Currently found {len(parts)}"
        )

    if len(assembly.fem.steps) > 1:
        logger.error("Sesam writer currently only supports 1 step. Will only use 1st step")

    part = parts[0]

    thick_map = dict()

    now = datetime.datetime.now()
    date_str = now.strftime("%d-%b-%Y")
    clock_str = now.strftime("%H:%M:%S")
    user = get_current_user()

    units = "UNITS     5.00000000E+00  1.00000000E+00  1.00000000E+00  1.00000000E+00\n          1.00000000E+00\n"

    assembly.consolidate_sections()
    assembly.consolidate_materials()
    materials = assembly.get_all_materials(True)

    inp_file_path = (analysis_dir / f"{name}T1").with_suffix(".FEM")

    if len(assembly.fem.steps) > 0:
        step = assembly.fem.steps[0]
        with open(analysis_dir / "sestra.inp", "w") as f:
            f.write(write_sestra_inp(name, step))

    # BNBCD is written before BLDEP (GeniE's own order) but its FIX codes are derived
    # from the BLDEP records, so build the records first and share them with both blocks.
    fems = [part.fem, assembly.fem]
    lin_deps = [r for fem in fems for r in bldep_records(fem)]
    # One derivation of the per-node dof count, shared by every record that declares an
    # NDOF (GNODE, BNBCD, BNMASS, BNLOAD) so they cannot disagree. See NodeDofs.
    ndofs = node_dofs(part.fem)
    # Explicit metadata if given, else the SESAM_SUPERNODES convention. See to_fem's
    # docstring for the precedence rule. ``ndofs`` keeps the convention from retaining a
    # rotation on a solid-only node, which has none.
    retained = retained_dofs(fems, metadata, ndofs)

    with open(inp_file_path, "w") as d:
        d.write(top_level_fem_str.format(date_str=date_str, clock_str=clock_str, user=user))
        d.write(units)
        d.write(materials_str(materials))
        d.write(sections_str(part.fem, thick_map))
        d.write(univec_str(part.fem))
        d.write(eccen_str(part.fem))
        d.writelines(nodes_gen(part.fem, ndofs))
        d.write(mass_str(part.fem, ndofs))
        d.write(sets_str(part.fem))
        d.write(bnbcd_str(fems, lin_deps, retained, ndofs))
        d.write("".join(r.to_str() for r in lin_deps))
        d.write(hinges_str(part.fem))
        d.writelines(elem_gen(part.fem, thick_map))
        d.write(loads_str(assembly.fem, ndofs) + loads_str(part.fem, ndofs))
        d.write("IEND                0.00            0.00            0.00            0.00\n")

    logger.info(f'Created an Sesam input deck at "{analysis_dir}"')


def materials_str(materials: list[Material]):
    out_str = "".join([write_ff("TDMATER", [(4, mat.id, 100 + len(mat.name), 0), (mat.name,)]) for mat in materials])

    out_str += "".join(
        [
            write_ff(
                "MISOSEL",
                [
                    (mat.id, mat.model.E, mat.model.v, mat.model.rho),
                    (mat.model.zeta, mat.model.alpha, 1, mat.model.sig_y),
                ],
            )
            for mat in materials
        ]
    )
    return out_str


#: ODOF (the list of the node's dofs) for each of the two NDOF values Sesam allows a
#: node to have. Manual printed 5-91: "solid type: NDOF=3, ODOF=123", "shell type:
#: NDOF=6, ODOF=123456".
_ODOF = {3: 123, 6: 123456}


def odof_for(ndof: int) -> int:
    """The ODOF field that goes with an NDOF of 3 or 6."""
    try:
        return _ODOF[int(ndof)]
    except KeyError:
        raise ValueError(f"A Sesam node has either 3 or 6 dofs, not {ndof}")


class NodeDofs:
    """How many dofs each node has — 3 or 6 — for every record that carries an NDOF.

    Why this exists: ada wrote ``NDOF=6, ODOF=123456`` on every GNODE. A solid
    (continuum) element has no rotational stiffness, so a node touched only by solids
    then has dofs 4-6 sitting on the stiffness diagonal with nothing in them, and
    Sestra's reduction aborts with "Matrix is not positive definite ... Degree of
    freedom no: 4". The Input Interface File manual (printed 5-91) is explicit that
    NDOF/ODOF "must be consistent with the type of node": 3/123 for a solid node,
    6/123456 for a shell node (a shell's drilling rotation carries no stiffness either,
    which shell elements handle with a small inserted term — hence the distinction).

    GNODE is not the only record with an NDOF field: BNBCD, BNMASS and BNLOAD each
    declare one and then carry exactly that many values. They all read their answer from
    here so the deck cannot contradict itself.

    Backed by two parallel arrays (ids sorted ascending + the dof count), so the node
    block can look up 689k nodes in one vectorised call while the handful of nodes with a
    BC, a mass or a load get a binary search each.
    """

    __slots__ = ("_ids", "_ndof")

    def __init__(self, node_ids, has_rotation):
        ids = np.asarray(node_ids, dtype=np.int64).reshape(-1)
        rot = np.asarray(has_rotation, dtype=bool).reshape(-1)
        if ids.size != rot.size:
            raise ValueError("node_ids and has_rotation must be the same length")
        order = np.argsort(ids, kind="stable")
        self._ids = ids[order]
        self._ndof = np.where(rot[order], 6, 3).astype(np.int8)

    def __len__(self) -> int:
        return int(self._ids.size)

    @property
    def node_ids(self) -> np.ndarray:
        return self._ids

    def ndof(self, nid: int) -> int:
        """This node's dof count. A node this object has never heard of is reported as
        6-dof: that is the historical answer, and the alternative is refusing to write a
        record for a node that is simply not in the mesh we were handed (a load on the
        assembly FEM referring to a part node, for instance)."""
        if self._ids.size == 0:
            return 6
        nid = int(nid)
        i = int(np.searchsorted(self._ids, nid))
        if i >= self._ids.size or int(self._ids[i]) != nid:
            return 6
        return int(self._ndof[i])

    def ndof_many(self, nids) -> np.ndarray:
        """Vectorised :meth:`ndof` — one gather instead of a call per node."""
        nids = np.asarray(nids, dtype=np.int64).reshape(-1)
        out = np.full(nids.size, 6, dtype=np.int8)
        if self._ids.size == 0 or nids.size == 0:
            return out
        i = np.searchsorted(self._ids, nids)
        np.clip(i, 0, self._ids.size - 1, out=i)
        hit = self._ids[i] == nids
        out[hit] = self._ndof[i[hit]]
        return out


#: "Every node has six dofs", i.e. what the writer did before per-node NDOF existed. The
#: default for the record writers that are handed no :class:`NodeDofs`.
ALL_SIX_DOF = NodeDofs(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool))


def _carries_rotational_stiffness(el) -> bool:
    """Whether this element gives its nodes rotational dofs.

    Everything that is not a continuum element does: beams and other line elements,
    shells, springs and connectors all have rotational terms (and the Sesam writer emits
    beams as BEAS / shells as the 6-dof shell types, which are 6-dof by definition). A
    ``Mass`` is the one element whose answer depends on its values rather than its shape:
    only a rotary inertia lands in BNMASS dofs 4-6, so a plain translational point mass
    on an otherwise solid-only node must not promote it to 6 dofs.
    """
    from ada.fem.elements import Mass

    if isinstance(el, Mass):
        from .write_masses import _bnmass_components

        return any(_bnmass_components(el, max(1, len(_elem_nodes(el))))[3:6])
    return not isinstance(el.type, SolidShapes)


def _elem_nodes(el) -> list:
    """The nodes one of the special (non-block) elements sits on.

    ``Mass`` keeps them on ``members`` -- ``Mass.fem_set``'s setter fills that in and
    leaves ``Elem._nodes`` alone, so a mass built before its set was attached has
    ``nodes is None`` -- and it is ``members`` that ``write_masses.mass_str`` writes the
    BNMASS records off, so it is ``members`` that has to decide the node's dof count.
    """
    from ada.fem.elements import Mass

    if isinstance(el, Mass):
        return list(el.members or el.nodes or ())
    return list(el.nodes or ())


def _ctype_carries_rotation(ctype) -> bool:
    """Same question as :func:`_carries_rotational_stiffness`, answered from a packed
    block's element type alone.

    A block is one element type, so the type settles it — including for the mass blocks
    ``MeshArrays.from_fem`` builds when an object FEM holding masses is converted: only
    ``ROTARYI`` writes into BNMASS dofs 4-6 (see ``write_masses._bnmass_components``), so
    a block of plain translational point masses must not promote its nodes. An element
    type this does not recognise is assumed to have rotations, which is what the writer
    assumed for everything before per-node NDOF existed.
    """
    if isinstance(ctype, SolidShapes):
        return False
    if isinstance(ctype, MassTypes):
        return ctype == MassTypes.ROTARYI
    if not isinstance(ctype, (LineShapes, ShellShapes, SpringTypes, ConnectorTypes)):
        logger.debug("sesam writer: element type %s assumed to have rotational dofs", ctype)
    return True


def node_dofs(fem: FEM) -> NodeDofs:
    """Derive each node's dof count from the mesh.

    A node is 6-dof if anything attached to it carries rotational stiffness or rotational
    mass, and 3-dof if the only things attached are continuum elements (or a purely
    translational mass). A node nothing is attached to stays 6-dof — it is not "a solid
    node", it is an unused node, and 6 is what the writer has always said about it.

    Cost. On the array-backed mesh this is one fancy-index write per element block --
    ``touched[blk.conn.ravel()] = True`` -- reached through the same packed store
    ``_nodes_as_arrays`` and ``write_elements._sesam_ordered_ids`` use, so it is
    proportional to the connectivity array rather than to a Python loop over elements: on
    the project model (689,596 nodes / 463,192 elements) it is a few tens of
    milliseconds. Only the special elements that live outside the blocks (Mass, Spring,
    Connector) are visited one at a time. The object mesh path has no packed
    connectivity, so there it does walk the elements.
    """
    node_store = getattr(fem.nodes, "store", None)
    elem_store = getattr(fem.elements, "store", None)

    if node_store is not None:
        node_ids = np.asarray(node_store.node_ids, dtype=np.int64)
    else:
        node_ids = np.fromiter((n.id for n in fem.nodes), dtype=np.int64, count=len(fem.nodes))

    n = node_ids.size
    # Two flags rather than one, so the outcome does not depend on the order the
    # elements happen to be visited in: rotational wins, and "no rotation and no
    # translation-only element either" means nothing is attached at all.
    rotational = np.zeros(n, dtype=bool)
    translational = np.zeros(n, dtype=bool)
    if n == 0:
        return NodeDofs(node_ids, rotational)

    if elem_store is not None:
        for ctype, blk in elem_store.blocks.items():
            rows = blk.conn.ravel()
            flags = rotational if _ctype_carries_rotation(ctype) else translational
            flags[rows] = True
        # Mass / Spring / Connector normally sit next to the store rather than in a block
        # (``ArrayElements._overflow``), as does anything added through ``add()``; those
        # have to be walked.
        extras = list(getattr(fem.elements, "_overflow", ()) or ())
    else:
        extras = list(fem.elements)

    if extras:
        id2idx = node_store.id2idx if node_store is not None else {int(nid): i for i, nid in enumerate(node_ids)}
        for el in extras:
            flags = rotational if _carries_rotational_stiffness(el) else translational
            for node in _elem_nodes(el):
                row = id2idx.get(int(node.id))
                if row is None:
                    continue
                flags[row] = True

    return NodeDofs(node_ids, rotational | ~translational)


def _nodes_as_arrays(fem: FEM) -> tuple[np.ndarray, np.ndarray]:
    """Return (node_ids, coords) sorted by ascending node id.

    On the array-backed mesh path the ids and coordinates already sit in the packed
    store, so sort the raw arrays instead of minting a proxy per node. The object
    path materialises the nodes once.
    """
    store = getattr(fem.nodes, "store", None)
    if store is not None:
        node_ids, coords = store.node_ids, store.coords
    else:
        nodes = list(fem.nodes)
        if not nodes:
            return np.zeros(0, dtype=np.int64), np.zeros((0, 3), dtype=float)
        node_ids = np.fromiter((n.id for n in nodes), dtype=np.int64, count=len(nodes))
        coords = np.asarray([(n[0], n[1], n[2]) for n in nodes], dtype=float)

    order = np.argsort(node_ids, kind="stable")
    return node_ids[order], coords[order]


def nodes_gen(fem: FEM, ndofs: NodeDofs | None = None) -> Iterator[str]:
    """Yield the GNODE + GCOORD records one at a time so the caller can stream them
    straight to file instead of holding a deck-sized string in memory.

    ``ndofs`` is the per-node dof count (:func:`node_dofs`); it is derived from ``fem``
    when not given. GNODE is where NDOF/ODOF are declared, so this function has no
    "assume 6" fallback — that assumption is the defect. Every other record that repeats
    an NDOF (BNBCD, BNMASS, BNLOAD) is handed the same object by ``to_fem``.
    """
    node_ids, coords = _nodes_as_arrays(fem)
    if node_ids.size == 0:
        yield "** No Nodes"
        return
    if ndofs is None:
        ndofs = node_dofs(fem)

    # Duplicate detection via adjacent-equality on the sorted id array: O(n), where a
    # ``not in`` scan over a growing list was O(n^2).
    dupes = np.flatnonzero(node_ids[1:] == node_ids[:-1])
    if dupes.size:
        raise Exception(
            'Doubly defined node id "{}". TODO: Make necessary code updates'.format(int(node_ids[dupes[0]]))
        )

    per_node = ndofs.ndof_many(node_ids)
    for nid, ndof in zip(node_ids, per_node):
        ndof = int(ndof)
        yield write_ff("GNODE", [(int(nid), int(nid), ndof, odof_for(ndof))])
    for nid, p in zip(node_ids, coords):
        yield write_ff("GCOORD", [(int(nid), p[0], p[1], p[2])])


def nodes_str(fem: FEM, ndofs: NodeDofs | None = None) -> str:
    return "".join(nodes_gen(fem, ndofs))


def bc_str(fem: FEM) -> str:
    """The BNBCD records for one FEM's boundary conditions alone.

    ``to_fem`` uses ``write_bcs.bnbcd_str`` instead, which also carries the BLDEP
    companion codes and the retained DOFs; this stays as the single-FEM shorthand.

    Three differences from the records this wrote before ``write_bcs`` existed, none of
    them a change of intent, all of them visible in deck text: records come out sorted by
    node id rather than in BC insertion order; two BCs naming one node merge into a single
    record holding the union of their DOFs, where this used to write two records a reader
    would see as contradicting each other; and a ``Bc`` on an element set is skipped with
    a warning instead of writing its *element* ids into the node field.
    """
    from .write_bcs import bnbcd_str

    return bnbcd_str([fem], ndofs=node_dofs(fem))


#: BELFIX OPT (manual printed 6-8) takes exactly two values, and they mean different
#: things about what A(1)..A(6) hold:
#:
#: * ``1`` -- A(i) is the *degree of fixation*, between 0 and 1: "a = 0, fully released;
#:   a = 1, fully connected".
#: * ``2`` -- A(i) is an interelement elastic spring stiffness, -1 meaning rigid.
#:
#: ``write_hinge`` below writes 0 for a released dof and 1 for a connected one, i.e.
#: degrees of fixation, so OPT is 1. The writer used to put 3 here, which is not a value
#: the manual defines at all -- the data was always OPT=1 data, only the field was wrong.
_BELFIX_OPT_DEGREE_OF_FIXATION = 1

#: BELFIX TRANO (same page): 0 means "A(i) is given in the local element coordinate
#: system", any other value references a transformation. A beam end release is expressed
#: about the beam's own axes -- releasing "the weak-axis bending moment" is a statement
#: about the local system -- so local is the correct frame here, deliberately and not
#: just by leaving a field at zero.
_BELFIX_TRANO_LOCAL = 0


def hinges_str(fem: FEM) -> str:
    out_str = ""
    h = Counter(1)

    def write_hinge(hinge):
        dofs = [0 if i in hinge else 1 for i in range(1, 7)]
        fix_id = next(h)
        data = [
            tuple([fix_id, _BELFIX_OPT_DEGREE_OF_FIXATION, _BELFIX_TRANO_LOCAL, 0]),
            tuple(dofs[:4]),
            tuple(dofs[4:]),
        ]
        return fix_id, write_ff("BELFIX", data)

    for el in fem.elements:
        h1, h2 = el.metadata.get("h1", None), el.metadata.get("h2", None)
        if h2 is None and h1 is None:
            continue
        h1_fix, h2_fix = 0, 0
        if h1 is not None:
            h1_fix, res_str = write_hinge(h1)
            out_str += res_str
        if h2 is not None:
            h2_fix, res_str = write_hinge(h2)
            out_str += res_str
        el.metadata["fixno"] = h1_fix, h2_fix

    return out_str


def univec_str(fem: FEM) -> str:
    out_str = ""
    uvec_id = Counter(1)

    unit_vecs = dict()

    def write_local_z(vec):
        tvec = tuple(vec)
        if tvec in unit_vecs.keys():
            return unit_vecs[tvec], None
        trans_no = next(uvec_id)
        data = [tuple([trans_no, *vec])]
        unit_vecs[tvec] = trans_no
        return trans_no, write_ff("GUNIVEC", data)

    # GUNIVEC is defined for beam elements only — Sesam element types 2, 15 and 23
    # (manual printed 6-92), i.e. ada's LINE / LINE3. It used to be written for every
    # sectioned element, and Presel then rejected the shells' GELREF1 with
    # "TRANSFORMATION NUMBER 1 DOES NOT EXSIST". A shell's ``local_z`` is its surface
    # normal (ada's own mesher passes ``local_z=normal``) and a solid section's is the
    # global-Z fallback in ``FemSection.local_z`` — neither is a transformation, and
    # the Sesam reader only reads ``transno`` back on the line-section path anyway.
    # Non-beam elements are left without a ``transno``, which ``write_elem`` writes as
    # TRANSNO = 0 (default element axes).
    #
    # Filtered off ``stru_elements`` rather than read from the container's ``lines``
    # view: on the array-backed container ``lines`` walks the mesh blocks only, while
    # ``elem_gen`` writes GELMNT1/GELREF1 off ``stru_elements``, which also covers
    # ``_overflow``. A beam that arrived through ``add()`` instead of inside a block
    # would otherwise be written without the GUNIVEC it needs and lose its orientation
    # silently.
    for el in fem.elements.stru_elements:
        if not isinstance(el.type, LineShapes):
            continue
        # Elements without a bound FemSection (e.g. mesh-only FEMs
        # coming through the cross-format converter) carry no local_z
        # to emit — skip them rather than crash. Sesam-native runs
        # always bind sections, so this guard is a no-op there.
        if el.fem_sec is None:
            continue
        local_z = el.fem_sec.local_z
        transno, res_str = write_local_z(local_z)
        if res_str is None:
            el.metadata["transno"] = transno
            continue
        out_str += res_str
        el.metadata["transno"] = transno

    return out_str


def eccen_str(fem: FEM) -> str:
    """GECCEN records for beam end eccentricities, plus the ``eccno`` each element refers to.

    The reader has understood GECCEN for a long time (``sesam/read/read_sections.py``
    and ``sesam/results/eccentricity.py``); the writer never emitted one, so a model
    with offset beams — a stiffener pushed onto its plate, a girder hung under its
    nodes — round-tripped through a Sesam deck with every beam back on its own axis.

    Sign convention: a GECCEN vector is a global offset to be ADDED to the node
    position to reach the beam end, which is the NEGATION of ``Beam.e1``/``e2`` (see
    ``ada.fem.meshing.utils.add_beam_ecc_to_elements`` and ``line_elem_to_beam`` for
    why the concept side carries the flipped sign). ``EccPoint.ecc_vector`` already
    holds the file's own sign, so it is written out as-is.
    ``tests/core/fem/formats/sesam/test_write_geccen.py`` is the proof: it builds
    beams with known ``e1``/``e2``, writes, reads back and compares.

    Deduplicated the way ``univec_str`` deduplicates GUNIVEC — decks repeat the same
    handful of offsets over thousands of stiffeners, and one record per element would
    dwarf the rest of the deck.

    The ``eccno`` is left on ``el.metadata`` for ``write_elem`` to pick up, mirroring
    how ``transno`` and ``fixno`` are handed over: an int when every node of the
    element shares one vector, and a per-node list when they differ, which GELREF1
    signals with ``eccno = -1`` and a tail.
    """
    from ..node_order import SESAM_ORDER

    out_str = ""
    ecc_id = Counter(1)
    ecc_nos: dict[tuple, int] = {}

    def write_ecc(vec) -> int:
        nonlocal out_str
        key = tuple(round(float(v), 10) for v in vec)
        if not any(key):
            # An all-zero offset is "no offset". Writing it as a record would make a
            # reader unable to tell the two apart.
            return 0
        ecc_no = ecc_nos.get(key)
        if ecc_no is not None:
            return ecc_no
        ecc_no = next(ecc_id)
        ecc_nos[key] = ecc_no
        out_str += write_ff("GECCEN", [(ecc_no, *key)])
        return ecc_no

    for el in fem.elements.lines_ecc:
        # Unsectioned elements are dropped by the GELREF1 emitter, so an eccno on one
        # would only leave an orphaned GECCEN behind.
        if el.fem_sec is None:
            continue
        ecc = el.eccentricity
        # The tail is read back node by node in the order GELMNT1 wrote the nodes, so
        # build it in Sesam's ordering rather than ada's (they differ for BTSS/LINE3).
        node_ids = [n.id for n in SESAM_ORDER.nodes_to_format(el.type, list(el.nodes))]
        per_node = [0] * len(node_ids)
        for end in (ecc.end1, ecc.end2):
            if end is None or end.ecc_vector is None:
                continue
            ecc_no = write_ecc(end.ecc_vector)
            if ecc_no == 0:
                continue
            node_id = end.node.id
            if node_id not in node_ids:
                logger.warning(
                    "sesam writer: element %s carries an eccentricity on node %s, which is not "
                    "one of its own nodes. Skipping that offset.",
                    el.id,
                    node_id,
                )
                continue
            per_node[node_ids.index(node_id)] = ecc_no

        if not any(per_node):
            continue
        if len(set(per_node)) == 1:
            el.metadata["eccno"] = per_node[0]
        else:
            el.metadata["eccno"] = per_node

    return out_str
