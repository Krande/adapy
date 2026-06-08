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

from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part


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

    merged = FEM(name=base.name, parent=base)
    merged.nodes = ArrayNodes(store, parent=merged)
    merged.elements = ArrayElements(store, fem_obj=merged)

    # ── re-key dependent references by the same per-part offsets ──────────────────────────
    # Node/element sets: prefix names, offset member ids. Keep an identity map (old set -> new
    # set) so section elsets and bc sets can be re-pointed to the merged copies.
    set_map: dict[int, FemSet] = {}
    merged_sets: list[FemSet] = []

    def _remap_set(p, s: FemSet) -> FemSet:
        existing = set_map.get(id(s))
        if existing is not None:
            return existing
        s.to_id_backed()
        off = node_off_of[id(p)] if s.type == SetTypes.NSET else el_off_of[id(p)]
        member_ids = [int(m) + off for m in (s._member_ids or [])]
        ns = FemSet(f"{prefix_of[id(p)]}_{s.name}", member_ids, s.type, parent=merged)
        set_map[id(s)] = ns
        merged_sets.append(ns)
        return ns

    for p in parts:
        for s in p.fem.sets:
            _remap_set(p, s)
    merged.sets = FemSets(merged_sets, parent=merged)

    # Sections: re-point each section's elset to the merged copy (creating it if the section
    # carried a standalone elset not in fem.sets) and carry the material.
    merged_sections: list = []
    for p in parts:
        for sec in p.fem.sections:
            if sec.elset is not None:
                sec.elset = _remap_set(p, sec.elset)
            sec.parent = merged
            merged_sections.append(sec)
    merged.sections = FemSections(merged_sections, fem_obj=merged)

    # Materials live on the owning Part; gather every part's into base's part.
    if base.parent is not None:
        for p in parts[1:]:
            if p.parent is not None and p.parent is not base.parent:
                base.parent.materials += p.parent.materials

    # Boundary conditions: re-point each bc's set to the merged copy.
    for p in parts:
        for bc in p.fem.bcs:
            if getattr(bc, "fem_set", None) is not None:
                bc.fem_set = _remap_set(p, bc.fem_set)
            bc.parent = merged
            merged.bcs.append(bc)

    # Masses, constraints, surfaces, local coordinate systems: carry across, re-pointing the
    # set references that masses/constraints hold.
    for p in parts:
        for name, mass in p.fem.masses.items():
            if getattr(mass, "elset", None) is not None:
                mass.elset = _remap_set(p, mass.elset)
            mass.parent = merged
            merged.masses[name] = mass
        for name, con in p.fem.constraints.items():
            con.parent = merged
            merged.constraints[name] = con
        for name, csys in p.fem.lcsys.items():
            csys.parent = merged
            merged.lcsys[name] = csys
        for name, surface in p.fem.surfaces.items():
            surface.parent = merged
            merged.surfaces[name] = surface

    base.fem = merged
    base.fem.parent = base
    for p in parts[1:]:
        p.fem = FEM(name=f"{p.name}_merged_away", parent=p)

    logger.info(f"Concatenated {len(parts)} FEM parts into '{base.name}' ({len(base.fem.nodes)} nodes)")
    return base
