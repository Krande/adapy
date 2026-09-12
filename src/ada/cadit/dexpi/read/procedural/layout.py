"""Placing a resolved P&ID: layout rules, connectivity grouping, and site terminals on a deck.

The build half of the import. Everything here needs deck bounds to have an answer at all --
generating the decks, packing the equipment onto them, seating a site terminal on a deck edge --
which is exactly why none of it belongs to reading a P&ID. The layout itself is
:func:`~ada.topo_model.layout.plan_layout`; this module supplies its rules, the grouping hint it
packs by, and the placement of the one thing the layout does not place, the off-page connector.
"""

from __future__ import annotations

from typing import Any

from ada.topo_model.layout import LayoutItem, LayoutRules
from ada.topology.entities import TopoSpace

from ..conventions import (
    DIRECTION_IN,
    SITE_ELEVATION,
    SITE_ELEVATION_FRACTION,
    SITE_INLET_FACE,
    SITE_OUTLET_FACE,
)
from .resolve import _Endpoint, _SegmentSpec

__all__ = [
    "_group_by_connectivity",
    "_layout_rules",
    "_no_none",
    "_place_site_terminals",
    "_restore_base_placements",
    "_space_of",
    "_stamp_equipment_metadata",
    "default_layout_rules",
]


def default_layout_rules() -> LayoutRules:
    """The layout rules a DEXPI import uses when the caller names none.

    ``deck_height`` is **explicit** and that is the whole point of this function: the pitch is
    uniform across the plan, so leaving it to "the tallest item plus headroom" lets a single 15 m
    ``ProcessColumn`` make every deck 16 m tall. A fixed 6 m storey with the column reported as
    poking through it is the more useful wrong answer.
    """
    return LayoutRules(max_length=24.0, max_width=12.0, deck_height=6.0)


def _layout_rules(layout: LayoutRules | dict | None) -> LayoutRules:
    if layout is None:
        return default_layout_rules()
    if isinstance(layout, LayoutRules):
        return layout
    if isinstance(layout, dict):
        rules = default_layout_rules()
        for key, value in layout.items():
            if not hasattr(rules, key):
                raise ValueError(f"unknown layout rule {key!r}; expected one of {sorted(vars(rules))}")
            setattr(rules, key, value)
        return rules
    raise TypeError(f"layout must be a LayoutRules, a dict or None, got {type(layout).__name__}")


def _group_by_connectivity(items: list[LayoutItem], segments: list[_SegmentSpec]) -> None:
    """Pack equipment joined by a run together, by giving each connected component of the process
    graph a shared :attr:`~ada.topo_model.layout.LayoutItem.group`.

    The cheapest possible answer to "shelf packing has no process sense": it does not know that a
    pump belongs beside its vessel, but it can at least keep the things a line touches on the same
    stretch of deck. The group key is the alphabetically first member, so it is deterministic and
    independent of the order the segments were read in.
    """
    parent: dict[str, str] = {item.name: item.name for item in items}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for spec in segments:
        connected = [end.equipment for end in spec.ends if end.equipment in parent]
        for other in connected[1:]:
            a, b = find(connected[0]), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)

    for item in items:
        if item.group is None:
            item.group = find(item.name)


def _place_site_terminals(segments: list[_SegmentSpec], spaces: list[TopoSpace], placements: dict) -> None:
    """Give every site terminal a world position on the boundary of a deck.

    A P&ID's off-page connector has no coordinate at all, so one is generated: inputs enter through
    :data:`~.conventions.SITE_INLET_FACE` of the deck their equipment stands on and outputs leave
    through :data:`~.conventions.SITE_OUTLET_FACE`, spread evenly along that face so two terminals
    never land on the same point. The direction
    vector points **into** the model, which is the direction the run leaves the boundary along --
    the routing engine follows it for one grid pitch before it starts pathfinding.
    """
    if not spaces:
        return
    by_name = {space.NAME: space for space in spaces}
    groups: dict[tuple[str, str], list[_Endpoint]] = {}
    for spec in segments:
        host = next(
            (by_name[name] for name in (_space_of(placements, end.equipment) for end in spec.ends) if name in by_name),
            spaces[0],
        )
        for end in spec.ends:
            if end.site is not None:
                face = SITE_INLET_FACE if end.direction == DIRECTION_IN else SITE_OUTLET_FACE
                groups.setdefault((host.NAME, face), []).append(end)

    for (space_name, face), members in sorted(groups.items()):
        space = by_name[space_name]
        elevation = min(SITE_ELEVATION, float(space.DZ) * SITE_ELEVATION_FRACTION)
        for i, end in enumerate(members):
            x = float(space.X) if face == SITE_INLET_FACE else float(space.X) + float(space.DX)
            y = float(space.Y) + float(space.DY) * (i + 1) / (len(members) + 1)
            end.position = (round(x, 6), round(y, 6), round(float(space.Z) + elevation, 6))
            end.direction_vector = (1.0, 0.0, 0.0) if face == SITE_INLET_FACE else (-1.0, 0.0, 0.0)


def _space_of(placements: dict, equipment_name: str | None) -> str | None:
    row = placements.get(equipment_name) if equipment_name else None
    return row.get("SPACE_NAME") if isinstance(row, dict) else None


def _restore_base_placements(out: dict, base_doc: dict | None) -> None:
    """Put back every equipment row ``base_doc`` already carried.

    ``apply_layout`` merges by name with the generated row winning, which is right for a fresh
    import and wrong for a re-import: an equipment a user has already positioned must keep the
    position they gave it. The generated row still sized the deck it sits on.
    """
    if not base_doc:
        return
    kept = {row.get("NAME"): row for row in base_doc.get("equipments") or [] if isinstance(row, dict)}
    if not kept:
        return
    out["equipments"] = [
        dict(kept[row["NAME"]]) if isinstance(row, dict) and row.get("NAME") in kept else row
        for row in out.get("equipments") or []
    ]


def _stamp_equipment_metadata(out: dict, provenance: dict[str, dict]) -> None:
    for row in out.get("equipments") or []:
        if isinstance(row, dict) and row.get("NAME") in provenance:
            metadata = dict(row.get("METADATA") or {})
            metadata.update(provenance[row["NAME"]])
            row["METADATA"] = metadata


def _no_none(row: Any) -> dict:
    return {k: v for k, v in row.items() if v is not None} if isinstance(row, dict) else row
