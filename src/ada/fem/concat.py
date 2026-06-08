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
self-consistent. The earlier ``FEM.__add__`` path mis-renumbered nodes vs. element refs and
parked the folded-in instance's elements in an ``_overflow`` list that the array readers skip,
which silently distorted or dropped merged-in instances.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part
    from ada.fem.results.common import Mesh


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


def concatenate_fem_to_single_part(assembly: "Assembly") -> "Part | None":
    """Fold all FEM-bearing parts of ``assembly`` into the first one's FEM, in place.

    No-op (returns the single part, or None) when there is nothing to merge. Returns the part
    that now holds the combined FEM."""
    from ada.api.mesh.containers import ArrayElements, ArrayNodes, to_array_backed
    from ada.api.mesh.store import ElemArrayBlock, MeshArrays
    from ada.fem import FEM
    from ada.fem.containers import FemSections, FemSets
    from ada.fem.sets import FemSet, SetTypes

    parts = [p for p in assembly.get_all_subparts(include_self=True) if p.fem is not None and len(p.fem.nodes) > 0]
    if len(parts) <= 1:
        return parts[0] if parts else None

    # Disambiguate set names with the source instance name when those are all distinct
    # (Abaqus multi-instance decks); otherwise fall back to the always-unique part name.
    inames = [p.fem.instance_name for p in parts]
    use_instance = all(inames) and len(set(inames)) == len(inames)
    prefix_of = {id(p): (p.fem.instance_name if use_instance else p.name) for p in parts}

    # Work on array-backed stores: a clean per-part store carries connectivity as row indices
    # plus the per-element section/elset reference lists.
    for p in parts:
        if not isinstance(p.fem.nodes, ArrayNodes):
            to_array_backed(p.fem)

    base = parts[0]

    coords_list: list[np.ndarray] = []
    nid_list: list[np.ndarray] = []
    merged_blocks: dict = {}  # ctype -> {conn, el_ids, fem_secs, elsets}
    row_off = node_off = el_off = 0
    node_off_of: dict[int, int] = {}
    el_off_of: dict[int, int] = {}
    for p in parts:
        st = p.fem.nodes.store
        node_off_of[id(p)] = node_off
        el_off_of[id(p)] = el_off
        coords_list.append(st.coords)
        nid_list.append(st.node_ids + node_off)
        p_nmax = int(st.node_ids.max()) if st.n_nodes else 0
        p_emax = 0
        for ctype, blk in st.blocks.items():
            entry = merged_blocks.setdefault(ctype, {"conn": [], "el_ids": [], "fem_secs": [], "elsets": []})
            entry["conn"].append(blk.conn.astype(np.int64) + row_off)
            entry["el_ids"].append(blk.el_ids + el_off)
            n = len(blk.el_ids)
            entry["fem_secs"].append(list(blk.fem_secs) if blk.fem_secs else [None] * n)
            entry["elsets"].append(list(blk.elsets) if blk.elsets else [None] * n)
            if n:
                p_emax = max(p_emax, int(blk.el_ids.max()))
        row_off += st.coords.shape[0]
        node_off += p_nmax
        el_off += p_emax

    blocks: dict = {}
    for ctype, entry in merged_blocks.items():
        conn = np.vstack(entry["conn"]).astype(np.int32)
        el_ids = np.concatenate(entry["el_ids"])
        fem_secs = [s for lst in entry["fem_secs"] for s in lst]
        elsets = [s for lst in entry["elsets"] for s in lst]
        blocks[ctype] = ElemArrayBlock(
            ctype,
            conn,
            el_ids,
            fem_secs=fem_secs if any(s is not None for s in fem_secs) else None,
            elsets=elsets if any(s is not None for s in elsets) else None,
        )
    store = MeshArrays(np.vstack(coords_list), np.concatenate(nid_list), blocks)

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
    # set) so section elsets and bc sets can be re-pointed to the merged copies. Read member ids
    # without to_id_backed() so the source set is not mutated.
    set_map: dict[int, FemSet] = {}
    merged_sets: list[FemSet] = []

    def _remap_set(p, s: FemSet) -> FemSet:
        existing = set_map.get(id(s))
        if existing is not None:
            return existing
        off = node_off_of[id(p)] if s.type == SetTypes.NSET else el_off_of[id(p)]
        mids = s._member_ids
        if mids is None:
            mids = [m.id for m in s.members]
        member_ids = [int(m) + off for m in mids]
        ns = FemSet(f"{prefix_of[id(p)]}_{s.name}", member_ids, s.type, parent=merged)
        set_map[id(s)] = ns
        merged_sets.append(ns)
        return ns

    for p in parts:
        for s in p.fem.sets:
            _remap_set(p, s)
    merged.sets = FemSets(merged_sets, parent=merged)

    # Sections: shallow copy, re-point the copy's elset to the merged set + carry the material
    # (copied once and re-pointed so the source sections stay intact).
    mat_map: dict[int, object] = {}
    merged_sections: list = []
    for p in parts:
        for sec in p.fem.sections:
            ns = copy.copy(sec)
            if ns.elset is not None:
                ns.elset = _remap_set(p, ns.elset)
            mat = getattr(ns, "material", None)
            if mat is not None:
                cm = mat_map.get(id(mat))
                if cm is None:
                    cm = copy.copy(mat)
                    mat_map[id(mat)] = cm
                    merged_part.add_material(cm)
                ns.material = cm
            ns.parent = merged
            merged_sections.append(ns)
    merged.sections = FemSections(merged_sections, fem_obj=merged)

    # Boundary conditions: shallow copy + re-point the copy's set.
    for p in parts:
        for bc in p.fem.bcs:
            nb = copy.copy(bc)
            if getattr(nb, "fem_set", None) is not None:
                nb.fem_set = _remap_set(p, nb.fem_set)
            nb.parent = merged
            merged.bcs.append(nb)

    # Masses / constraints / surfaces / local coordinate systems: shallow copy across, re
    # pointing the set references masses hold.
    for p in parts:
        for name, mass in p.fem.masses.items():
            nm = copy.copy(mass)
            if getattr(nm, "elset", None) is not None:
                nm.elset = _remap_set(p, nm.elset)
            nm.parent = merged
            merged.masses[name] = nm
        for name, con in p.fem.constraints.items():
            nc = copy.copy(con)
            nc.parent = merged
            merged.constraints[name] = nc
        for name, csys in p.fem.lcsys.items():
            merged.lcsys[name] = csys
        for name, surface in p.fem.surfaces.items():
            merged.surfaces[name] = surface

    logger.info(f"Concatenated {len(parts)} FEM parts into '{merged_part.name}' ({len(merged.nodes)} nodes)")
    return merged_part
