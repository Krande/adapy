"""Concatenate the FEMs of a multi-part/instance assembly into one part's FEM.

Single-part FEM writers (Sesam ``.FEM``, Code_Aster ``.med``, Genie ``.xml``) can only emit
one part. A FEM imported from a multi-instance deck (e.g. Abaqus assembly with several part
instances) carries one ``Part.fem`` per instance, with independently-numbered nodes/elements
and possibly same-named sets across instances. This folds them into a single ``Part.fem``.

The merge is a direct store-level concatenation rather than a chain of ``FEM.__add__`` folds:
each part is materialised as an array store (connectivity is node *row-index* based, so a per
part row offset is all the geometry needs), and node/element ids are offset by the running max
so they stay globally unique. The same per-part id offsets are reused to re-key every dependent
reference — node/element sets, section elsets, bc sets and masses — so the result is fully
self-consistent.

"Store-level" means the WHOLE store: a merged ``ElemArrayBlock`` has to carry every one of
its ``__slots__``, not just connectivity and ids, and the Mass/Spring/Connector objects the
array container holds beside their packed rows have to come with it. A merge that rebuilt
blocks from ``conn``/``el_ids``/``fem_secs``/``elsets`` alone dropped beam end
eccentricities, hinges, per-element metadata and every point mass — silently, and only on
the multi-part path, since this is what stands between a multi-part model and the
single-part writers. The earlier ``FEM.__add__`` path mis-renumbered nodes vs. element refs and
parked the folded-in instance's elements in an ``_overflow`` list that the array readers skip,
which silently distorted or dropped merged-in instances.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger
from ada.core.guid import create_guid
from ada.fem.exceptions.model_definition import DoesNotSupportMultiPart
from ada.fem.formats import conversion_report

if TYPE_CHECKING:
    from ada.api.mesh.store import MeshArrays
    from ada.api.spatial import Assembly, Part
    from ada.fem.results.common import Mesh


def _merged_node(node, store: "MeshArrays", node_off: int):
    """The merged store's stand-in for a source part's node reference.

    A node reference held on a *side* object — an ``EccPoint``, a ``Hinge``, a ``Mass``'s
    member list — is not connectivity, so the block merge does not touch it: it still names
    the source part's node by that part's own id. The merge shifts node ids by ``node_off``,
    so the reference has to be shifted with them or it names a different node (or none) in
    the merged numbering — which is exactly what ``eccen_str`` reports as "carries an
    eccentricity on node N, which is not one of its own nodes" before dropping the offset.

    Resolved to the merged store's proxy (not the source object ``Node``/proxy) for the
    reason ``ArrayElements._rebind_special_to_store`` gives: an object ``Node`` holds every
    element that references it in ``Node.refs``, so one retained reference pins a whole
    part's object mesh, and its coordinates would no longer follow ``ArrayNodes.move``.
    Anything the merged store doesn't know (a bare int, an unpacked node) is left alone.
    """
    nid = getattr(node, "id", None)
    if nid is None:
        return node
    nid = int(nid) + node_off
    if not store.has_node(nid):
        return node
    return store.node_proxy_by_id(nid)


def _merged_ecc(ecc, store: "MeshArrays", node_off: int):
    """A copy of ``Eccentricity`` whose ends name the merged store's nodes.

    Copied rather than re-pointed in place: the source part keeps its own FEM in the
    assembly tree, and an eccentricity re-keyed onto the merged numbering would be wrong
    there (``concatenate_fem_to_single_part`` is non-destructive by contract)."""
    from ada.fem.elements import EccPoint

    new = copy.copy(ecc)
    for attr in ("end1", "end2"):
        end = getattr(ecc, attr, None)
        if end is None:
            continue
        # EccPoint keeps an array-backed node as (store, id) rather than as a proxy, so
        # going through its own setter is what keeps that indirection intact.
        ne = EccPoint(None, end.ecc_vector)
        node = end.node
        if node is not None:
            ne.node = _merged_node(node, store, node_off)
        setattr(new, attr, ne)
    return new


def _merged_hinge(hinge, store: "MeshArrays", node_off: int):
    """A copy of ``HingeProp`` whose ends' ``fem_node`` names the merged store's nodes.

    ``Hinge.concept_node`` is deliberately left alone: it is a node of the *concept* beam
    (``ada.Beam.n1``/``n2``), not a member of the FEM numbering this merge offsets."""
    new = copy.copy(hinge)
    for attr in ("end1", "end2"):
        end = getattr(hinge, attr, None)
        if end is None:
            continue
        ne = copy.copy(end)
        if getattr(ne, "fem_node", None) is not None:
            ne.fem_node = _merged_node(ne.fem_node, store, node_off)
        setattr(new, attr, ne)
    return new


def _retained_elems(fem) -> "list[tuple[object, bool]]":
    """The element *objects* an array-backed container holds, as ``(elem, is_packed)``.

    Two homes, matching ``ArrayElements._iter_specials``: ``_packed_specials`` (a
    Mass/Spring/Connector whose row *is* in a block — the object is kept because a block has
    nowhere to put a mass value, a spring stiffness or a connector section) and ``_overflow``
    (anything handed to ``add()``, which is where the readers put masses; those have no block
    row at all). A block-only merge carries neither, so ``fem.elements.masses`` came out empty
    and every writer that iterates it — Sesam BNMASS, Abaqus ``*Mass``, Usfos NODEMASS —
    emitted nothing."""
    els = fem.elements
    return [(e, True) for e in getattr(els, "_packed_specials", ())] + [
        (e, False) for e in getattr(els, "_overflow", ())
    ]


def _rebind_special(elem, store: "MeshArrays", node_off: int) -> None:
    """``ArrayElements._rebind_special_to_store`` with the merge's node-id offset applied.

    Same attributes for the same reasons (``Mass`` keeps its nodes on ``_members``,
    ``Spring``/``Connector`` also cache their ends on ``_n1``/``_n2``); the difference is
    that here the node the reference names is the *source* part's id, so it is resolved
    through ``_merged_node`` instead of straight through the store."""
    for attr in ("_nodes", "_members"):
        seq = getattr(elem, attr, None)
        if seq:
            setattr(elem, attr, [_merged_node(n, store, node_off) for n in seq])
    for attr in ("_n1", "_n2"):
        node = getattr(elem, attr, None)
        if node is not None:
            setattr(elem, attr, _merged_node(node, store, node_off))


def concatenate_fem_meshes(parts: "list[Part]") -> "tuple[Mesh, list[tuple[int, int]]]":
    """Merge the FEM *meshes* of ``parts`` into one ``Mesh`` **without** mutating the assembly.

    Unlike :func:`concatenate_fem_to_single_part` (which folds everything into one part's FEM
    and empties the others), this leaves every part's FEM intact — use it when the multipart
    structure must stay in the assembly tree (e.g. mesh/visualisation export that shouldn't
    alter the model). Each part's individually-correct ``to_mesh()`` is concatenated with a per
    part row offset for connectivity (node row-index based) and running-max id offsets for
    global uniqueness; sections/materials/vectors/elem_data are merged with the same offsets so
    the beam-solid tessellation still resolves.

    Returns ``(mesh, part_offsets)`` where ``part_offsets[i] = (node_id_offset, el_id_offset)``
    for ``parts[i]``, so callers can re-key per-part set/group member ids onto the merged
    numbering. ``mesh`` carries row-index node_refs (the standard ``Mesh`` convention)."""
    import numpy as np

    from ada.fem.results.common import ElementBlock, FemNodes, Mesh

    coords_parts: list = []
    id_parts: list = []
    blocks: list = []
    sections: dict = {}
    materials: dict = {}
    vectors: dict = {}
    elem_data_parts: list = []
    part_offsets: list[tuple[int, int]] = []
    row_off = node_max = el_max = sec_max = mat_max = vec_max = 0
    for p in parts:
        m = p.fem.to_mesh()
        nid_off, elid_off = node_max, el_max
        part_offsets.append((nid_off, elid_off))
        coords = np.asarray(m.nodes.coords)
        pids = np.asarray(m.nodes.identifiers, dtype=np.int64) + nid_off
        coords_parts.append(coords)
        id_parts.append(pids)
        for b in m.elements:
            conn = np.asarray(b.node_refs)
            conn = conn + row_off if b.node_refs_are_indices else conn + nid_off
            elids = np.asarray(b.identifiers, dtype=np.int64) + elid_off
            blocks.append(ElementBlock(b.elem_info, conn, elids, node_refs_are_indices=b.node_refs_are_indices))
        if m.sections:
            for sid, s in m.sections.items():
                sections[int(sid) + sec_max] = s
        if m.materials:
            for mid, mat in m.materials.items():
                materials[int(mid) + mat_max] = mat
        if m.vectors:
            for vid, v in m.vectors.items():
                vectors[int(vid) + vec_max] = v
        if m.elem_data is not None and len(m.elem_data):
            ed = np.asarray(m.elem_data, dtype=np.int64).copy()
            ed[:, 0] += elid_off
            ed[:, 1] += mat_max
            ed[:, 2] += sec_max
            ed[:, 3] += vec_max
            elem_data_parts.append(ed)
        row_off += len(coords)
        node_max = int(pids.max()) + 1
        el_max = max(
            (int(np.asarray(b.identifiers).max()) + 1 + elid_off for b in m.elements if len(b.identifiers)),
            default=el_max,
        )
        sec_max = (max(sections) + 1) if sections else sec_max
        mat_max = (max(materials) + 1) if materials else mat_max
        vec_max = (max(vectors) + 1) if vectors else vec_max

    mesh = Mesh(
        elements=blocks,
        nodes=FemNodes(np.vstack(coords_parts), np.concatenate(id_parts)),
        sections=sections or None,
        materials=materials or None,
        vectors=vectors or None,
        elem_data=(np.vstack(elem_data_parts) if elem_data_parts else None),
    )
    return mesh, part_offsets


#: The stage merge findings are recorded under (``conversion_report``).
_STAGE = "fem merge"


def _fem_parts(assembly: "Part") -> "list[Part]":
    return [p for p in assembly.get_all_subparts(include_self=True) if p.fem is not None and len(p.fem.nodes) > 0]


def concatenate_fem_to_single_part(assembly: "Assembly") -> "Part | None":
    """Fold all FEM-bearing parts of ``assembly`` into one standalone part's FEM.

    No-op (returns the single part, or None) when there is nothing to merge. Returns the part
    that holds the combined FEM: the parts' nodes, elements, sets, sections, Bcs, masses,
    constraints, surfaces, local systems and steps, re-keyed to the merged ids and set names, and
    the same of any part in between that holds no nodes itself.

    What ``assembly.fem`` itself holds (when it has no nodes of its own) is *not* carried: a writer
    handed the original assembly reads it from there. :func:`single_part_assembly` carries it too,
    and is what :func:`ada.fem.formats.general.write_to_fem` hands a single-part writer."""
    parts = _fem_parts(assembly)
    if len(parts) <= 1:
        return parts[0] if parts else None
    merged_part, _ = _merge(assembly, parts, top_fem=None)
    return merged_part


def single_part_assembly(assembly: "Assembly") -> "Assembly":
    """A temporary assembly holding one part: the non-destructive merge of every FEM-bearing part
    of ``assembly``, for the writers that write one part (Sesam, Code_Aster, CalculiX, ...).

    Everything the model's steps, supports, masses, constraints and surfaces name is re-keyed into
    the merged numbering and set names -- those on the parts, and those on ``assembly.fem`` (an
    assembly-level Bc on a part's node, a step load on a part's set). The assembly's own steps are
    on the returned assembly's FEM, the parts' steps on the merged part's FEM: where a writer looks
    for them on a single-part model. Anything the merge cannot re-key is refused by name
    (:class:`DoesNotSupportMultiPart`): a deck that names a source id writes a load or a support on
    another part's node, silently.

    Returns ``assembly`` itself when at most one part has nodes."""
    from ada import Assembly

    parts = _fem_parts(assembly)
    if len(parts) <= 1:
        return assembly
    write_assembly = Assembly(assembly.name, units=assembly.units)
    merged_part, top_steps = _merge(assembly, parts, top_fem=write_assembly.fem)
    write_assembly.add_part(merged_part)
    write_assembly.fem.steps = top_steps
    return write_assembly


def refuse_assembly_data_the_merge_leaves_behind(assembly: "Assembly") -> None:
    """For a writer that merges a multi-part model itself (:func:`get_fem_model_from_assembly`) and
    then reads the assembly's own Bcs, sets, constraints and steps from the unmerged model: those
    name the source parts' sets and ids, which the merge renames and shifts. Refused by name rather
    than written onto the wrong nodes; :meth:`Assembly.to_fem` carries them
    (:func:`single_part_assembly`)."""
    if len(_fem_parts(assembly)) <= 1:
        return
    fem = assembly.fem
    held = []
    for label, items in (
        ("Bc", [bc.name for bc in fem.bcs]),
        ("constraint", list(fem.constraints)),
        ("mass", list(fem.masses)),
        ("surface", list(fem.surfaces)),
        ("set", [s.name for s in fem.sets]),
    ):
        held += [f"{label} {name!r}" for name in items]
    for step in fem.steps:
        for obj in [*step.loads, *step.bcs.values(), *step.hist_outputs, *step.interactions.values()]:
            if _names_a_set(obj):
                held.append(f"{type(obj).__name__} {obj.name!r} of step {step.name!r}")
    if held:
        raise DoesNotSupportMultiPart(
            f"the assembly {assembly.name!r} merges {len(_fem_parts(assembly))} parts into one, which renames and "
            f"renumbers their sets and nodes, and it holds {', '.join(held)} on the parts' sets and ids. Write it "
            "through Assembly.to_fem, which carries them into the merged numbering."
        )


def _names_a_set(obj) -> bool:
    from ada.fem.sets import FemSet
    from ada.fem.surfaces import Surface

    for value in vars(obj).values():
        values = value if isinstance(value, (list, tuple)) else [value]
        if any(isinstance(v, (FemSet, Surface)) for v in values):
            return True
    return False


def _merge(assembly: "Part", parts: "list[Part]", top_fem) -> "tuple[Part, list]":
    """The merge behind :func:`concatenate_fem_to_single_part` (``top_fem=None``: the assembly's own
    FEM is left where it is) and :func:`single_part_assembly` (``top_fem``: the FEM that receives
    the assembly's re-keyed steps; its other data goes onto the merged part)."""
    from ada.api.mesh.containers import ArrayElements, ArrayNodes, to_array_backed
    from ada.api.mesh.store import ElemArrayBlock, MeshArrays
    from ada.fem import FEM
    from ada.fem.containers import FemSections, FemSets
    from ada.fem.sets import FemSet, SetTypes
    from ada.fem.surfaces import Surface

    # Parts in the tree that hold no nodes but may hold sets, Bcs or steps naming the parts'
    # nodes and elements. The assembly itself is one only when its data is carried (``top_fem``).
    merged_ids = {id(p) for p in parts}
    carriers = [
        p
        for p in assembly.get_all_subparts(include_self=True)
        if id(p) not in merged_ids and p.fem is not None and (top_fem is not None or p is not assembly)
    ]
    for p in parts + carriers:
        _refuse_what_the_merge_does_not_carry(p, is_carrier=id(p) not in merged_ids)

    # Disambiguate set names with the source instance name when those are all distinct
    # (Abaqus multi-instance decks); otherwise fall back to the always-unique part name.
    # Only a name that more than one part uses is prefixed: the others are the model's own
    # names, and prefixing them all renamed every set of a model whose sets never clashed.
    inames = [p.fem.instance_name for p in parts]
    use_instance = all(inames) and len(set(inames)) == len(inames)
    prefix_of = {id(p): (p.fem.instance_name if use_instance else p.name) for p in parts}
    prefix_of.update({id(p): p.name for p in carriers})
    set_name_count: dict = {}
    surface_name_count: dict = {}
    for p in parts + carriers:
        for s in p.fem.sets:
            set_name_count[s.name.lower()] = set_name_count.get(s.name.lower(), 0) + 1
        for name in p.fem.surfaces:
            surface_name_count[name.lower()] = surface_name_count.get(name.lower(), 0) + 1

    # Work on array-backed stores: a clean per-part store carries connectivity as row indices
    # plus the per-element section/elset reference lists.
    for p in parts:
        if not isinstance(p.fem.nodes, ArrayNodes):
            to_array_backed(p.fem)

    base = parts[0]

    coords_list: list[np.ndarray] = []
    nid_list: list[np.ndarray] = []
    # ctype -> {conn, el_ids, fem_secs, elsets, sparse, rows}. An ElemArrayBlock's payload is
    # all of __slots__, not just conn/el_ids: ``fem_secs``/``elsets`` are per-row *lists* and
    # concatenate row-wise, while ``ecc``/``hinge``/``metadata`` are sparse dicts keyed by the
    # row INSIDE the block. Carrying the first two and dropping the last three is what made a
    # multi-part export lose every beam end eccentricity (GECCEN), hinge (BELFIX via the
    # ``h1``/``h2`` metadata) and per-element metadata the single-part path writes.
    merged_blocks: dict = {}
    row_off = node_off = el_off = 0
    node_off_of: dict[int, int] = {}
    el_off_of: dict[int, int] = {}
    # A part is shifted only when its ids collide with a part already merged. The ids are the
    # model's own, and a writer keeps them (a Sesam deck's node and element numbers are the
    # adapy ids); shifting every part after the first renumbered models whose parts never
    # overlapped -- an assembly-level reference node 900 beside part nodes 1..6 turned the
    # part's nodes into 901..906.
    used_nids: set = set()
    used_eids: set = set()
    for p in parts:
        st = p.fem.nodes.store
        p_nids = {int(i) for i in st.node_ids}
        p_eids = {int(i) for blk in st.blocks.values() for i in blk.el_ids}
        p_eids |= {int(e.id) for e, _ in _retained_elems(p.fem) if e.id is not None}
        p_node_off = node_off if p_nids & used_nids else 0
        p_el_off = el_off if p_eids & used_eids else 0
        used_nids |= {i + p_node_off for i in p_nids}
        used_eids |= {i + p_el_off for i in p_eids}
        node_off_of[id(p)] = p_node_off
        el_off_of[id(p)] = p_el_off
        if p_node_off or p_el_off:
            # The ids are what a single-part writer writes and what results are keyed by, so a
            # changed one is said, not left to be found in the output.
            conversion_report.current().approximated(
                _STAGE,
                "Part",
                p.name,
                "its node and element ids collide with another part's and are renumbered in the merged FEM",
                node_offset=int(p_node_off),
                element_offset=int(p_el_off),
            )
        coords_list.append(st.coords)
        nid_list.append(st.node_ids + p_node_off)
        for ctype, blk in st.blocks.items():
            entry = merged_blocks.setdefault(
                ctype,
                {"conn": [], "el_ids": [], "fem_secs": [], "elsets": [], "formulations": [], "sparse": [], "rows": 0},
            )
            entry["conn"].append(blk.conn.astype(np.int64) + row_off)
            entry["el_ids"].append(blk.el_ids + p_el_off)
            n = len(blk.el_ids)
            entry["fem_secs"].append(list(blk.fem_secs) if blk.fem_secs else [None] * n)
            entry["elsets"].append(list(blk.elsets) if blk.elsets else [None] * n)
            entry["formulations"].append(list(blk.formulations) if blk.formulations else [None] * n)
            # The row-keyed side tables are merged once the store exists, since their node
            # references resolve against it. Note the three distinct offsets: ``row_off`` is a
            # NODE row (connectivity), ``entry["rows"]`` an ELEMENT row inside this block, and
            # ``node_off``/``el_off`` are id offsets.
            entry["sparse"].append((entry["rows"], p_node_off, blk))
            entry["rows"] += n
        # The special elements sitting in ``_overflow`` have no block row, so their ids are
        # invisible to the block loop; they are in ``p_eids`` all the same, or a mass on one
        # part collides with an element on the next.
        row_off += st.coords.shape[0]
        node_off = max(used_nids, default=0)
        el_off = max(used_eids, default=0)

    blocks: dict = {}
    for ctype, entry in merged_blocks.items():
        conn = np.vstack(entry["conn"]).astype(np.int32)
        el_ids = np.concatenate(entry["el_ids"])
        fem_secs = [s for lst in entry["fem_secs"] for s in lst]
        elsets = [s for lst in entry["elsets"] for s in lst]
        formulations = [f for lst in entry["formulations"] for f in lst]
        blocks[ctype] = ElemArrayBlock(
            ctype,
            conn,
            el_ids,
            fem_secs=fem_secs if any(s is not None for s in fem_secs) else None,
            elsets=elsets if any(s is not None for s in elsets) else None,
            formulations=formulations if any(f is not None for f in formulations) else None,
        )
    store = MeshArrays(np.vstack(coords_list), np.concatenate(nid_list), blocks)

    for ctype, entry in merged_blocks.items():
        mblk = blocks[ctype]
        for brow, nd_off, src in entry["sparse"]:
            for row, ecc in src.ecc.items():
                mblk.ecc[brow + row] = _merged_ecc(ecc, store, nd_off)
            for row, hinge in src.hinge.items():
                mblk.hinge[brow + row] = _merged_hinge(hinge, store, nd_off)
            for row, md in src.metadata.items():
                # Copied, not shared: the writers mutate an element's metadata in place
                # (``el.metadata["transno"] = ...``), and the source part is not ours to edit.
                mblk.metadata[brow + row] = dict(md)

    # Build a STANDALONE merged part — never mutate the source assembly (the parts keep their
    # FEMs in the tree, so writing to a single-part format doesn't collapse the model). Every
    # dependent object that needs re-keying (sections / bcs / masses / materials) is shallow
    # copied before its set/material refs are re-pointed, so the originals stay untouched.
    from ada import Part

    merged_part = Part(base.name)
    merged_part.units = base.units
    merged = FEM(name=base.name, parent=merged_part)
    merged_part.fem = merged
    merged.nodes = ArrayNodes(store, parent=merged)
    merged.elements = ArrayElements(store, fem_obj=merged)

    # ── re-key dependent references by the same per-part offsets ──────────────────────────
    # Node/element sets: prefix names, offset member ids. Keep an identity map (old set -> new
    # set) so section elsets, bc sets and everything a step names can be re-pointed to the merged
    # copies. Read member ids without to_id_backed() so the source set is not mutated.
    set_map: dict[int, FemSet] = {}
    merged_sets: list[FemSet] = []
    taken_set_names: set = set()
    part_of_fem = {id(p.fem): p for p in parts}
    owner_of_fem = {id(p.fem): p for p in parts + carriers}

    def _member_offset(m, p, s: FemSet, offsets: dict) -> int:
        # Each member by the part that owns it: an assembly-level set holds nodes of several parts,
        # and each part's ids moved by that part's own offset.
        owner = part_of_fem.get(id(getattr(m, "parent", None)))
        if owner is not None:
            return offsets[id(owner)]
        if id(p) in offsets:
            return offsets[id(p)]
        raise DoesNotSupportMultiPart(
            f"set {s.name!r} of {p.name!r} holds {type(m).__name__} {getattr(m, 'id', m)}, which belongs to no part "
            "of the merged model, so its id in the merged numbering is unknown"
        )

    def _remap_set(p, s: FemSet) -> FemSet:
        existing = set_map.get(id(s))
        if existing is not None:
            return existing
        offsets = node_off_of if s.type == SetTypes.NSET else el_off_of
        mids = s._member_ids
        if mids is not None:
            # Ids alone: resolved in the set's own part. A set of a part with no nodes names ids of
            # parts it cannot tell apart; that is only safe while no part was renumbered.
            if id(p) in offsets:
                off = offsets[id(p)]
            elif not any(offsets.values()):
                off = 0
            else:
                raise DoesNotSupportMultiPart(
                    f"set {s.name!r} of {p.name!r} names its members by id only, and the merge renumbers the parts' "
                    "ids, so which part each one belongs to is unknown"
                )
            member_ids = [int(m) + off for m in mids]
        else:
            member_ids = [int(m.id) + _member_offset(m, p, s, offsets) for m in s.members]
        name = s.name if set_name_count.get(s.name.lower(), 0) <= 1 else f"{prefix_of[id(p)]}_{s.name}"
        key = (s.type, name.lower())
        if key in taken_set_names:
            # A set no FEM lists (reached through a step or a surface) whose name is taken.
            name = f"{prefix_of[id(p)]}_{s.name}"
            key = (s.type, name.lower())
            if key in taken_set_names:
                raise DoesNotSupportMultiPart(
                    f"set {s.name!r} of {p.name!r}: the merged model already has a set {name!r}"
                )
        taken_set_names.add(key)
        if name != s.name:
            conversion_report.current().approximated(
                _STAGE,
                "FemSet",
                s.name,
                "another part has a set of that name; renamed in the merged FEM",
                new_name=name,
            )
        ns = FemSet(name, member_ids, s.type, parent=merged)
        set_map[id(s)] = ns
        merged_sets.append(ns)
        return ns

    for p in parts + carriers:
        for s in p.fem.sets:
            _remap_set(p, s)

    def _node_off_for(node) -> int:
        owner = part_of_fem.get(id(getattr(node, "parent", None)))
        return node_off_of[id(owner)] if owner is not None else 0

    # Special element OBJECTS (Mass / Spring / Connector). Their rows travel with the blocks
    # above, but the values that make them what they are live only on the object, so without
    # this the merged part keeps the rows and loses every mass, stiffness and connector
    # section — ``ArrayElements._warn_on_unbacked_special_blocks`` names this merge as the
    # case it exists to catch. Each object is copied (the source part keeps its own), moved
    # onto its row's merged element id and re-pointed at the merged store.
    #
    # Before ``FemSets`` is built, not after: that constructor resolves every set member
    # eagerly, and an ``_overflow`` special has no block row to be found by — its own elset
    # ("<mass name>_set", written by ``FEM.add_mass``) can only resolve once the object is
    # in the merged container.
    for p in parts:
        nd_off, e_off = node_off_of[id(p)], el_off_of[id(p)]
        for el, is_packed in _retained_elems(p.fem):
            ns = copy.copy(el)
            if el.id is not None:
                # The same ``el_off`` the block's el_ids got, so the object and the row it
                # stands for keep naming one element (cf. ArrayElements.renumber).
                ns._el_id = int(el.id) + e_off
            _rebind_special(ns, store, nd_off)
            md = getattr(el, "_metadata", None)
            if isinstance(md, dict):
                ns._metadata = dict(md)
            # Assigned to the private attributes: ``Mass.fem_set``'s setter rebuilds
            # ``_members`` from the set, which would undo the rebinding just done above.
            for attr in ("_fem_set", "_elset"):
                fs = getattr(el, attr, None)
                if fs is not None:
                    setattr(ns, attr, _remap_set(p, fs))
            ns.parent = merged
            target = merged.elements._packed_specials if is_packed else merged.elements._overflow
            target.append(ns)

    # Sections: shallow copy, re-point the copy's elset to the merged set + carry the material
    # (copied once and re-pointed so the source sections stay intact).
    #
    # Materials are named, not numbered, in every writer (and the merged part holds one per name:
    # ``Materials.add`` hands back the one it has). Two parts' materials of one name and equal
    # properties are one material; of one name and different properties the second is prefixed
    # like a set -- left alone, PartB's elements were written with PartA's material.
    mat_map: dict[int, object] = {}

    def _merged_material(p, mat):
        cm = mat_map.get(id(mat))
        if cm is not None:
            return cm
        held = merged_part.materials.name_map.get(mat.name)
        if held is not None and _same_material_model(held.model, mat.model):
            mat_map[id(mat)] = held
            held_refs = {id(r) for r in held.refs}
            held.refs.extend(r for r in mat.refs if id(r) not in held_refs)
            return held
        cm = copy.copy(mat)
        # Its own list: a shared one gained the other parts' objects in the user's material.
        cm._refs = list(mat.refs)
        if held is not None:
            new_name = f"{prefix_of[id(p)]}_{mat.name}"
            if new_name in merged_part.materials.name_map:
                raise DoesNotSupportMultiPart(
                    f"material {mat.name!r} of {p.name!r} differs from another part's material of that name, and the "
                    f"merged model already has a material {new_name!r}"
                )
            cm._name = new_name
            cm._guid = create_guid()
            conversion_report.current().approximated(
                _STAGE,
                "Material",
                mat.name,
                "another part has a material of that name with different properties; renamed in the merged FEM",
                new_name=new_name,
            )
        mat_map[id(mat)] = cm
        merged_part.add_material(cm)
        return cm

    merged_sections: list = []
    for p in parts:
        for sec in p.fem.sections:
            ns = copy.copy(sec)
            if ns.elset is not None:
                ns.elset = _remap_set(p, ns.elset)
            mat = getattr(ns, "material", None)
            if mat is not None:
                ns.material = _merged_material(p, mat)
            ns.parent = merged
            merged_sections.append(ns)
    merged.sections = FemSections(merged_sections, fem_obj=merged)

    # Surfaces: copied with their sets re-pointed, and prefixed like a set when another part
    # has one of the same name (they are keyed by name in the merged FEM, where the second
    # used to replace the first). Before the steps, whose pressure loads name them.
    surface_map: dict[int, object] = {}

    def _remap_surface(p, sf):
        existing = surface_map.get(id(sf))
        if existing is not None:
            return existing
        if sf.id_refs and (id(p) not in merged_ids or node_off_of[id(p)] or el_off_of[id(p)]):
            raise DoesNotSupportMultiPart(
                f"surface {sf.name!r} of {p.name!r} names its faces by element id, and the merge renumbers that "
                "part's elements"
            )
        nsf = copy.copy(sf)
        fs = sf.fem_set
        if isinstance(fs, list):
            nsf._fem_set = [_remap_set(p, x) for x in fs]
        elif fs is not None:
            nsf._fem_set = _remap_set(p, fs)
        nsf._refs = []
        if surface_name_count.get(sf.name.lower(), 0) > 1 or sf.name in merged.surfaces:
            new_name = f"{prefix_of[id(p)]}_{sf.name}"
            if new_name in merged.surfaces:
                raise DoesNotSupportMultiPart(
                    f"surface {sf.name!r} of {p.name!r}: the merged model already has a surface {new_name!r}"
                )
            nsf._name = new_name
            conversion_report.current().approximated(
                _STAGE,
                "Surface",
                sf.name,
                "another part has a surface of that name; renamed in the merged FEM",
                new_name=new_name,
            )
        nsf.parent = merged
        surface_map[id(sf)] = nsf
        merged.surfaces[nsf.name] = nsf
        return nsf

    for p in parts + carriers:
        for surface in p.fem.surfaces.values():
            _remap_surface(p, surface)

    # Boundary conditions: shallow copy + re-point the copy's set. One FEM holds one Bc per name
    # (``FEM.add_bc``), and a writer may name the Bc in its deck (a Code_Aster concept), where the
    # second of two parts' "fix" replaced the first and left that part unsupported -- so a name
    # more than one part uses is prefixed, like a set's.
    bc_name_count: dict = {}
    for p in parts + carriers:
        for bc in p.fem.bcs:
            bc_name_count[bc.name.lower()] = bc_name_count.get(bc.name.lower(), 0) + 1
    for p in parts + carriers:
        for bc in p.fem.bcs:
            nb = copy.copy(bc)
            if getattr(nb, "fem_set", None) is not None:
                nb.fem_set = _remap_set(p, nb.fem_set)
            if bc_name_count[bc.name.lower()] > 1:
                nb._name = f"{prefix_of[id(p)]}_{bc.name}"
                conversion_report.current().approximated(
                    _STAGE,
                    "Bc",
                    bc.name,
                    "another part has a Bc of that name; renamed in the merged FEM",
                    new_name=nb.name,
                )
            nb.parent = merged
            merged.bcs.append(nb)

    # Masses / constraints / local coordinate systems: shallow copy across, re pointing the set
    # references masses hold. Keyed by name in the merged FEM, so a second one of a name is
    # refused rather than left to replace the first.
    for p in parts + carriers:
        for name, mass in p.fem.masses.items():
            nm = copy.copy(mass)
            if getattr(nm, "elset", None) is not None:
                nm.elset = _remap_set(p, nm.elset)
            nm.parent = merged
            _put_once(merged.masses, name, nm, "mass", p)
        for name, con in p.fem.constraints.items():
            nc = copy.copy(con)
            # Re-pointed like a BC's set. A constraint kept on its source sets named the
            # source ids, which the merge may have shifted -- a coupling at assembly level
            # then wrote BLDEP records on nodes the deck does not have. A set is found in
            # ``set_map`` whichever part owns it (every part's sets are mapped above); a
            # node, named by an equation term, is shifted by its own part's offset.
            for attr in ("_m_set", "_s_set"):
                op = getattr(nc, attr, None)
                if isinstance(op, FemSet):
                    setattr(nc, attr, _remap_set(p, op))
                elif isinstance(op, Surface):
                    setattr(nc, attr, _remap_surface(p, op))
            if nc.equation_terms is not None:
                nc._equation_terms = tuple(
                    (
                        _remap_set(p, ref) if isinstance(ref, FemSet) else _merged_node(ref, store, _node_off_for(ref)),
                        dof,
                        coef,
                    )
                    for ref, dof, coef in nc.equation_terms
                )
            nc.parent = merged
            _put_once(merged.constraints, name, nc, "constraint", p)
        for name, csys in p.fem.lcsys.items():
            merged.lcsys[name] = csys

    # Steps: copied with every set, surface and load they name re-pointed into the merged FEM.
    # The steps of the parts (and of nodeless parts in between) go to the merged FEM, the
    # assembly's own to ``top_fem``; the user's steps are not touched. A step object that names
    # a set or surface of no merged part, or a bare node or element, is refused by name.
    #
    # A set no FEM of the merge holds is still the parts' business when its members are: a load's
    # set is never adopted by ``Step.add_load`` (the repo's own examples build loads on a fresh
    # ``FemSet``), so it reaches the merge with no parent. Its part is read off its members, and
    # ``_remap_set`` offsets each member by its own part, as for any carrier's set (members of
    # several parts included); the part found here only prefixes the set's name if that clashes.
    def _owner_of_set(s, where: str):
        owner = owner_of_fem.get(id(s.parent))
        if owner is not None:
            return owner
        if s._member_ids is not None:
            raise DoesNotSupportMultiPart(
                f"{where} names set {s.name!r}, which belongs to no part of the merged model and names its members "
                "by id only, so which part each one belongs to is unknown"
            )
        owners = []
        for m in s.members:
            o = part_of_fem.get(id(getattr(m, "parent", None)))
            if o is None:
                raise DoesNotSupportMultiPart(
                    f"{where} names set {s.name!r}, whose {type(m).__name__} {getattr(m, 'id', m)} belongs to no part "
                    "of the merged model; its merged id is unknown"
                )
            owners.append(o)
        return owners[0] if owners else base

    def _set_for(s, where: str):
        mapped = set_map.get(id(s))
        if mapped is not None:
            return mapped
        return _remap_set(_owner_of_set(s, where), s)

    def _surface_for(sf, where: str):
        mapped = surface_map.get(id(sf))
        if mapped is not None:
            return mapped
        owner = owner_of_fem.get(id(sf.parent))
        if owner is None:
            # A surface no FEM holds: the part of the sets it is made of.
            fs = sf.fem_set
            sets = [x for x in (fs if isinstance(fs, list) else [fs]) if x is not None]
            if not sets:
                raise DoesNotSupportMultiPart(
                    f"{where} names surface {sf.name!r}, which belongs to no part of the merged model and is made of "
                    "no set; its faces cannot be given their merged ids"
                )
            owner = [_owner_of_set(x, f"{where}, surface {sf.name!r}") for x in sets][0]
        return _remap_surface(owner, sf)

    step_copies: dict[int, object] = {}

    def _rekey_value(value, where: str):
        from ada.api.nodes import Node
        from ada.fem.common import FemBase
        from ada.fem.elements import Elem
        from ada.fem.surfaces import Surface

        if isinstance(value, FemSet):
            return _set_for(value, where)
        if isinstance(value, Surface):
            return _surface_for(value, where)
        if isinstance(value, (Node, Elem)):
            raise DoesNotSupportMultiPart(
                f"{where} names {type(value).__name__} {value.id} directly; the merge renumbers the parts' ids and "
                "re-keys only what a step names through a set or a surface"
            )
        if isinstance(value, FemBase):
            return _rekey_obj(value, where)
        if isinstance(value, list):
            new = [_rekey_value(v, where) for v in value]
            return value if all(a is b for a, b in zip(new, value)) else new
        if isinstance(value, tuple):
            new = tuple(_rekey_value(v, where) for v in value)
            return value if all(a is b for a, b in zip(new, value)) else new
        if isinstance(value, dict):
            new = {k: _rekey_value(v, where) for k, v in value.items()}
            return value if all(new[k] is v for k, v in value.items()) else new
        return value

    def _rekey_obj(obj, where: str):
        from ada.fem.constraints import Bc
        from ada.fem.steps import Step

        done = step_copies.get(id(obj))
        if done is not None:
            return done
        new = copy.copy(obj)
        step_copies[id(obj)] = new
        here = f"{where}, {type(obj).__name__} {getattr(obj, 'name', '')!r}"
        for attr, value in vars(obj).items():
            if attr in ("_parent", "_fem_obj"):
                continue
            nv = _rekey_value(value, here)
            if nv is value:
                continue
            if attr == "_fem_set" and isinstance(new, Bc):
                new.fem_set = nv  # keeps the set's refs
            else:
                setattr(new, attr, nv)
        if isinstance(new, Step):
            # Its loads, Bcs and outputs name the copy as their step.
            for child in _children(new):
                child.parent = new
        return new

    def _rekey_steps(steps, fem) -> list:
        out = []
        for step in steps:
            ns = _rekey_obj(step, f"step {step.name!r}")
            ns.parent = fem
            out.append(ns)
        return out

    for p in assembly.get_all_subparts(include_self=True):
        # The assembly's own steps go to ``top_fem``, or stay where the writer reads them.
        if id(p.fem) in owner_of_fem and p is not assembly:
            merged.steps += _rekey_steps(p.fem.steps, merged)
    top_steps = _rekey_steps(assembly.fem.steps, top_fem) if top_fem is not None else []

    # Built last: every set above, including one first reached through a step or a surface, is
    # in it.
    merged.sets = FemSets(merged_sets, parent=merged)

    logger.info(f"Concatenated {len(parts)} FEM parts into '{merged_part.name}' ({len(merged.nodes)} nodes)")
    return merged_part, top_steps


def _children(obj) -> list:
    """The step objects ``obj`` holds that carry ``obj`` as their parent (a step's loads, Bcs,
    outputs, interactions, load cases)."""
    from ada.fem.common import FemBase

    out = []
    for attr in ("_loads", "_bcs", "_load_cases", "_interactions", "_hist_outputs", "_field_outputs"):
        value = getattr(obj, attr, None)
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, list):
            out += [v for v in value if isinstance(v, FemBase)]
    return out


def _same_material_model(a, b) -> bool:
    """Whether two material models write the same deck: every property ``Metal.unique_props`` lists
    (E, v, rho, yield and ultimate stress, plasticity, thermal and damping data). A model without that
    comparison is the same only as itself."""
    if a is b:
        return True
    if type(a) is not type(b) or not hasattr(a, "equal_props"):
        return False
    return a.equal_props(b)


def _put_once(container: dict, name: str, obj, label: str, part) -> None:
    if name in container:
        raise DoesNotSupportMultiPart(
            f"{label} {name!r} of {part.name!r}: another part has a {label} of that name, and the merged FEM keeps "
            "one per name"
        )
    container[name] = obj


def _refuse_what_the_merge_does_not_carry(p, *, is_carrier: bool) -> None:
    """Data a merge would otherwise drop without a word."""
    fem = p.fem
    dropped = []
    if fem.predefined_fields:
        dropped += [f"predefined field {n!r}" for n in fem.predefined_fields]
    if fem.initial_state is not None:
        dropped.append(f"initial state {fem.initial_state.name!r}")
    if fem.interactions:
        dropped += [f"interaction {n!r}" for n in fem.interactions]
    if is_carrier and (len(fem.elements) > 0 or fem.masses):
        # Elements (point masses, springs) on a part with no nodes of its own: they name other
        # parts' nodes, which the element merge does not re-key.
        dropped.append(f"{len(fem.elements)} element(s) and {len(fem.masses)} mass(es) on other parts' nodes")
    if dropped:
        raise DoesNotSupportMultiPart(
            f"{p.name!r} holds {', '.join(dropped)}, which merging the model into one part does not carry"
        )
