"""Merging systems: the branched fold at a junction, and the union into a base document.

:func:`_fold_branch_groups` is the one place the import makes *fewer* systems than the P&ID has
segments -- the legs meeting at a 3+-way junction become a single branched
:class:`~ada.topology.entities.TopoSystem` whose metadata (the keys in
:mod:`ada.api.systems.branch_meta`) lets the compiler rebuild the tree and the writer split it back.
:func:`_merge_systems` is the last step of a build: generated systems into whatever a base
document already carried, by name.
"""

from __future__ import annotations

from ada.api.systems.branch_meta import (
    BRANCH_JUNCTION_ID,
    BRANCH_KEY,
    BRANCH_LEG_METADATA,
    BRANCH_LEGS,
)
from ada.topology.entities import TopoSystem

from ...model import DexpiDocument, DexpiItem
from .resolve import _Endpoint, _SegmentSpec

__all__ = ["_fold_branch_groups", "_merge_systems"]


def _fold_branch_groups(
    doc: DexpiDocument, segments: list["_SegmentSpec"], junctions: dict[str, list[str]]
) -> list["_SegmentSpec"]:
    """Merge the segments meeting at a 3+-way junction into one branched :class:`_SegmentSpec`.

    Each of those segments already resolved its junction-facing end to its OWN dedicated port on
    the junction equipment (:func:`_junction_equipment` gives a tee exactly three), so nothing about
    connectivity changes here -- this only changes how many :class:`~ada.topology.entities.TopoSystem`
    rows that connectivity becomes. The merged system's ``ends`` is every leg's two endpoints
    concatenated leg by leg, so the ``CONNECTIONS`` list :func:`resolved_to_procedural_doc` later
    builds from ``ends`` is N pairs in that same order; ``METADATA["branch"]["legs"]`` names each
    leg (the ORIGINAL per-segment name, e.g. ``"L100/1"``) in that order too, which is what
    :func:`~ada.topo_model.compile._wire_systems` reads to rebuild the tree and what
    :mod:`ada.cadit.dexpi.write.from_ada` reads to split a branched System back into the segments
    the source had.

    A degree-2 junction (in ``junctions`` but with only two owning segments) is a pass-through, not
    a branch -- left as two separate two-ended systems, unchanged. A leg that failed
    :func:`_segment_spec` already reported its own reason and is missing from ``segments``; the
    whole junction is then left unfolded rather than merging a partial, disconnected set. Likewise
    when one of a junction's segments directly joins it to ANOTHER 3+-way junction (no equipment
    between two tees) and that other junction folded first: taking the shared segment into this
    merge too would duplicate its two endpoints across both branched systems, so this junction is
    left unfolded rather than double-booking it -- a real but rare shape (adjacent branch points
    with no run between them) that stays Stage-2-unsupported and two-ended-per-segment, same as
    before this function existed.
    """
    by_id = {spec.item.id: spec for spec in segments}
    folded: list[_SegmentSpec] = []
    consumed: set[str] = set()
    for junction_id, segment_ids in junctions.items():
        if len(segment_ids) < 3 or any(sid in consumed for sid in segment_ids):
            continue
        legs = [by_id[sid] for sid in segment_ids if sid in by_id]
        if len(legs) != len(segment_ids):
            continue
        consumed.update(segment_ids)
        junction_item = doc.items[junction_id]
        first = legs[0].entity
        merged_ends: list[_Endpoint] = []
        leg_names: list[str] = []
        leg_metadata: list[dict] = []
        components: list[DexpiItem] = []
        for leg in legs:
            merged_ends.extend(leg.ends)
            leg_names.append(leg.entity.NAME)
            leg_metadata.append({"name": leg.entity.NAME, "dexpi": leg.entity.METADATA.get("dexpi", {})})
            components.extend(leg.components)
        entity = TopoSystem(
            NAME=f"branch-{(junction_item.tag or junction_item.id).strip()}",
            TYPE=first.TYPE,
            MEDIUM=first.MEDIUM,
            CONNECTIONS=[],
            METADATA={
                BRANCH_KEY: {
                    BRANCH_JUNCTION_ID: junction_id,
                    BRANCH_LEGS: leg_names,
                    BRANCH_LEG_METADATA: leg_metadata,
                },
            },
        )
        folded.append(_SegmentSpec(item=junction_item, entity=entity, ends=merged_ends, components=components))
    folded.extend(spec for spec in segments if spec.item.id not in consumed)
    return folded


def _merge_systems(existing: list, generated: list[TopoSystem]) -> list[dict]:
    """Union the generated systems into ``existing`` on ``NAME``, generated winning in place."""
    merged = [dict(row) if isinstance(row, dict) else row for row in existing]
    at = {row.get("NAME"): i for i, row in enumerate(merged) if isinstance(row, dict) and row.get("NAME")}
    for entity in generated:
        row = entity.model_dump(mode="json", exclude_none=True)
        if row["NAME"] in at:
            merged[at[row["NAME"]]] = row
        else:
            at[row["NAME"]] = len(merged)
            merged.append(row)
    return merged
