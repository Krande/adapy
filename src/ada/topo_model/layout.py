"""Rule-based topology generation: an equipment list in, decks + placements out.

The procedural engine can compile a document (:mod:`ada.topo_model.compile`) but
it cannot *invent* one: every :class:`~ada.topology.entities.TopoEquipment` must
name a cell it sits in or on, and something has to decide where the cells are.
This module is that something. Given a list of equipment with nothing but a
physical size, it generates the :class:`~ada.topology.entities.TopoSpace` decks
to house them and places each item on one, producing the ``spaces`` +
``equipments`` a :class:`~ada.topo_model.builder.ProceduralBuilder` needs.

It is deliberately domain-neutral — a P&ID importer, a catalog dump or a
hand-written list of tags are all equally valid input — and its output is plain
entity objects, so a caller merges them into a document with :func:`apply_layout`
and compiles as usual.

**What this is not.** The v1 packer is *shelf packing*: sort by footprint,
fill rows along +X, start a new row when the row is full and a new deck when the
deck is full. It has no process sense whatsoever. A pump can land at the far end
of a deck from the vessel it feeds, and nothing here knows or cares that the two
are connected — the result is a plausible, routable plant, not a good one. The
grouping hook (:attr:`LayoutItem.group` / :attr:`LayoutRules.group_key`) is the
seam for teaching it more; and once a model is compiled,
:func:`ada.topo_model.relocate.propose_relocations` is the existing tool that
looks at the *routed* result and proposes the moves that clear the runs which
did not route cleanly. Plan first, relocate after.

Three entry points:

* :func:`plan_layout` — items + rules -> a :class:`LayoutPlan`.
* :func:`apply_layout` — merge a plan into a procedural document.
* :func:`validate_equipment_in_cells` — the standing rule, checkable on any
  document: every equipment sits in or on a cell.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Hashable, Sequence

from ada.config import logger
from ada.topology.entities import TopoEquipment, TopoSpace

__all__ = [
    "LayoutItem",
    "LayoutRules",
    "LayoutPlan",
    "plan_layout",
    "apply_layout",
    "validate_equipment_in_cells",
]

_TOL = 1e-9


@dataclass
class LayoutItem:
    """One piece of equipment to place, as the layout engine needs it.

    ``name`` is the tag (it becomes ``TopoEquipment.NAME``) and ``type_slug`` the
    catalog/archetype key written to ``DESCRIPTION`` — the compiler resolves the
    render geometry and ports from it. ``lx/ly/lz`` are the physical extents in
    metres; ``rot_z`` spins the item about its footprint centre (the packer
    reserves the ROTATED footprint, so a spun item still fits its slot).

    ``cog`` is the centre of gravity offset the entity requires (``(dx, dy, dz)``
    from the footprint centre / base); it defaults to the box centroid.
    ``group`` clusters items that belong together — see :class:`LayoutRules`.
    ``fixed`` pre-places the item at a WORLD ``(x, y, z)``: the packer leaves it
    exactly there and packs the rest around it, which is what makes re-planning
    an existing model non-destructive.
    """

    name: str
    type_slug: str
    lx: float
    ly: float
    lz: float
    mass: float = 0.0
    cog: tuple[float, float, float] | None = None
    rot_z: float = 0.0
    group: str | None = None
    fixed: tuple[float, float, float] | None = None


@dataclass
class LayoutRules:
    """The bounds and clearances the packer works within.

    ``max_length``/``max_width`` are the deck's full X/Y extents; usable space is
    that minus ``edge_clearance`` on each side. ``aisle`` is the minimum gap kept
    between any two footprints (and between a footprint and a pre-placed item).

    ``deck_height`` is UNIFORM across the plan — an explicit value, or the
    tallest item's ``lz`` plus ``headroom``. One pitch for every deck keeps the
    elevations predictable, which is what lets a ``fixed`` item be assigned back
    to a deck by its world Z when a model is re-planned.

    ``group_key`` overrides :attr:`LayoutItem.group` as the clustering key (any
    hashable). v1 packs each group contiguously — every member of a group is
    placed before the next group starts. The later "cluster equipment by room
    requirement" work derives the key from those requirements and emits one cell
    per group instead of one per deck; neither changes this signature.
    """

    max_length: float = 20.0
    max_width: float = 10.0
    deck_height: float | None = None
    headroom: float = 1.0
    aisle: float = 1.5
    edge_clearance: float = 1.0
    cell_prefix: str = "Deck"
    group_key: Callable[[LayoutItem], Hashable] | None = None


@dataclass
class LayoutPlan:
    """The generated topology: the decks, the placements, and what did not fit.

    ``unplaced`` names every item the bounds could not hold — oversize items are
    reported, never silently dropped — and ``warnings`` collects everything else
    worth a human's attention (a pre-placed item outside its deck, a deck height
    that clips an item, ...).
    """

    spaces: list[TopoSpace] = field(default_factory=list)
    placements: list[TopoEquipment] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _footprint(item: LayoutItem) -> tuple[float, float]:
    """The item's plan footprint, as the axis-aligned box that contains it once
    ``rot_z`` is applied. Rotation pivots on the footprint centre (matching
    :func:`ada.topo_model.equipment.apply_equipment_rotation`), so reserving this
    extent and centring the item in it places the spun box exactly in its slot."""
    a = math.radians(float(item.rot_z or 0.0))
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return float(item.lx) * c + float(item.ly) * s, float(item.lx) * s + float(item.ly) * c


def _blocker_right_edge(
    x: float, y: float, fx: float, fy: float, obstacles: Sequence[tuple[float, float, float, float]], aisle: float
) -> float | None:
    """The right (+X) edge to sweep past when the footprint ``(x, y, fx, fy)``
    clashes with a pre-placed obstacle, or ``None`` when the slot is free.
    Obstacles are inflated by ``aisle`` so the gap rule holds against them too;
    the FURTHEST right edge of all clashing obstacles is returned, so one sweep
    clears a whole cluster."""
    right: float | None = None
    for ox0, oy0, ox1, oy1 in obstacles:
        if (
            x < ox1 + aisle - _TOL
            and ox0 - aisle + _TOL < x + fx
            and y < oy1 + aisle - _TOL
            and oy0 - aisle + _TOL < y + fy
        ):
            right = ox1 if right is None else max(right, ox1)
    return right


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #
def _group_of(item: LayoutItem, rules: LayoutRules) -> Hashable:
    """The clustering key: ``group_key`` when given, else ``LayoutItem.group``.
    An item with no group is its own singleton cluster (keyed by name), so an
    ungrouped list packs in plain size order."""
    key = rules.group_key(item) if rules.group_key is not None else item.group
    return key if key is not None else ("\x00item", item.name)


def _ordered(items: Sequence[LayoutItem], rules: LayoutRules) -> list[LayoutItem]:
    """Items in pack order: descending footprint area, ties broken by descending
    height then by name — fully deterministic, no dependence on input order.
    Groups are then kept contiguous, each group taking the position of its
    largest member (a stable re-sort, so the within-group order survives)."""

    def _rank(it: LayoutItem) -> tuple[float, float, str]:
        fx, fy = _footprint(it)
        return (-(fx * fy), -float(it.lz), it.name)

    by_size = sorted(items, key=_rank)
    group_rank: dict[Hashable, int] = {}
    for it in by_size:
        group_rank.setdefault(_group_of(it, rules), len(group_rank))
    return sorted(by_size, key=lambda it: group_rank[_group_of(it, rules)])


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #
def _pack_floor(
    remaining: deque[LayoutItem],
    obstacles: list[tuple[float, float, float, float]],
    rules: LayoutRules,
    floor_index: int,
    placed: list[tuple[int, LayoutItem, float, float, float]],
    unplaced: list[str],
    warnings: list[str],
) -> None:
    """Shelf-pack as much of ``remaining`` onto one deck as fits, popping what it
    places. Rows run along +X at a row depth of the row's tallest footprint;
    a footprint that no longer fits the row starts the next one, and a row that no
    longer fits the deck ends the pass (the caller opens the next deck)."""
    x0 = float(rules.edge_clearance)
    x1 = float(rules.max_length) - float(rules.edge_clearance)
    y0 = float(rules.edge_clearance)
    y1 = float(rules.max_width) - float(rules.edge_clearance)

    row_y = y0
    cursor = x0
    row_depth = 0.0

    while remaining:
        item = remaining[0]
        fx, fy = _footprint(item)
        if fx > (x1 - x0) + _TOL or fy > (y1 - y0) + _TOL:
            remaining.popleft()
            unplaced.append(item.name)
            warnings.append(
                f"{item.name}: footprint {fx:.3f} x {fy:.3f} m does not fit a "
                f"{x1 - x0:.3f} x {y1 - y0:.3f} m deck (max_length/max_width minus 2*edge_clearance)"
            )
            continue

        # Sweep along the row past any pre-placed item in the way.
        x = cursor
        fits = False
        while x + fx <= x1 + _TOL:
            blocked = _blocker_right_edge(x, row_y, fx, fy, obstacles, float(rules.aisle))
            if blocked is None:
                fits = True
                break
            x = blocked + float(rules.aisle)

        if not fits:
            step = row_depth + float(rules.aisle)
            if step <= _TOL:  # a zero aisle on an empty row would not advance
                step = fy if fy > _TOL else 1.0
            row_y += step
            cursor = x0
            row_depth = 0.0
            if row_y + fy > y1 + _TOL:
                return  # deck is full; the caller opens the next one
            continue

        # The slot holds the ROTATED footprint; the entity's X/Y are the
        # un-rotated corner, so centre the box in the slot it was given.
        placed.append((floor_index, item, x + (fx - float(item.lx)) / 2.0, row_y + (fy - float(item.ly)) / 2.0, 0.0))
        remaining.popleft()
        cursor = x + fx + float(rules.aisle)
        row_depth = max(row_depth, fy)


def _deck_height(items: Sequence[LayoutItem], rules: LayoutRules, warnings: list[str]) -> float:
    """The uniform deck pitch: the explicit ``deck_height``, else the tallest item
    plus ``headroom``. An explicit pitch shorter than an item is honoured (the
    caller may know better) but reported."""
    if rules.deck_height is not None:
        height = float(rules.deck_height)
        for it in items:
            if float(it.lz) > height + _TOL:
                warnings.append(f"{it.name}: lz {float(it.lz):.3f} m exceeds deck_height {height:.3f} m")
        return height
    tallest = max((float(it.lz) for it in items), default=0.0)
    return tallest + float(rules.headroom)


def plan_layout(items: Sequence[LayoutItem], rules: LayoutRules | None = None) -> LayoutPlan:
    """Generate the decks to house ``items`` and place each item on one.

    Items are ordered deterministically (descending footprint area, then height,
    then name), with each ``group`` kept contiguous, and shelf-packed along +X
    into rows within ``max_length``/``max_width`` less ``edge_clearance``; a full
    row starts the next row, a full deck starts the next deck one ``deck_height``
    up. Every deck is a :class:`~ada.topology.entities.TopoSpace` named
    ``Deck1``, ``Deck2``, ... (see :attr:`LayoutRules.cell_prefix`) and every
    placement a :class:`~ada.topology.entities.TopoEquipment` seated on that
    deck's floor in CELL-LOCAL coordinates (``GLOBAL_COORDS=False``).

    An item with :attr:`LayoutItem.fixed` keeps its world position: it is
    assigned to the deck its Z falls on and becomes an obstacle the packer works
    around. An item too large for the bounds is returned in
    :attr:`LayoutPlan.unplaced` by name.

    The packing is geometric only — see the module docstring on what that costs.
    """
    rules = rules or LayoutRules()
    items = list(items)
    plan = LayoutPlan()
    if not items:
        return plan

    if rules.max_length - 2 * rules.edge_clearance <= 0 or rules.max_width - 2 * rules.edge_clearance <= 0:
        raise ValueError("edge_clearance leaves no usable deck area; check max_length/max_width/edge_clearance")

    height = _deck_height(items, rules, plan.warnings)
    if height <= 0:
        raise ValueError("deck height resolved to zero; pass an explicit LayoutRules.deck_height")

    ordered = _ordered(items, rules)
    # (floor index, item, cell-local X, cell-local Y, cell-local Z)
    placed: list[tuple[int, LayoutItem, float, float, float]] = []
    obstacles: dict[int, list[tuple[float, float, float, float]]] = {}

    # Pre-placed items first: they define their own deck (by world Z) and are the
    # obstacles the packer must respect.
    for item in [it for it in ordered if it.fixed is not None]:
        wx, wy, wz = (float(v) for v in item.fixed)
        index = max(0, int(math.floor((wz + _TOL) / height)))
        local_z = wz - index * height
        fx, fy = _footprint(item)
        # The obstacle is the ROTATED footprint, centred on the un-rotated box.
        ox, oy = wx + (float(item.lx) - fx) / 2.0, wy + (float(item.ly) - fy) / 2.0
        placed.append((index, item, wx, wy, local_z))
        obstacles.setdefault(index, []).append((ox, oy, ox + fx, oy + fy))
        if local_z + float(item.lz) > height + _TOL:
            plan.warnings.append(
                f"{item.name}: pre-placed at z={wz:.3f} m, its top passes through the deck above "
                f"({rules.cell_prefix}{index + 2})"
            )
        if ox < -_TOL or oy < -_TOL or ox + fx > rules.max_length + _TOL or oy + fy > rules.max_width + _TOL:
            plan.warnings.append(f"{item.name}: pre-placed footprint falls outside {rules.cell_prefix}{index + 1}")

    remaining: deque[LayoutItem] = deque(it for it in ordered if it.fixed is None)
    floor_index = 0
    while remaining:
        before = len(remaining)
        _pack_floor(remaining, obstacles.get(floor_index, []), rules, floor_index, placed, plan.unplaced, plan.warnings)
        if not remaining:
            break
        if len(remaining) == before and not obstacles.get(floor_index):
            # Defensive: an empty deck that placed nothing cannot make progress.
            stuck = remaining.popleft()
            plan.unplaced.append(stuck.name)
            plan.warnings.append(f"{stuck.name}: could not be placed on an empty deck")
            continue
        floor_index += 1

    n_floors = max([floor_index + 1] + [i + 1 for i, _, _, _, _ in placed])
    plan.spaces = [
        TopoSpace(
            NAME=f"{rules.cell_prefix}{i + 1}",
            INCLUDE=True,
            X=0.0,
            Y=0.0,
            Z=i * height,
            DX=float(rules.max_length),
            DY=float(rules.max_width),
            DZ=height,
        )
        for i in range(n_floors)
    ]
    plan.placements = [
        _to_equipment(item, f"{rules.cell_prefix}{index + 1}", x, y, z) for index, item, x, y, z in placed
    ]
    for message in plan.warnings:
        logger.warning("layout: %s", message)
    return plan


def _to_equipment(item: LayoutItem, space_name: str, x: float, y: float, z: float) -> TopoEquipment:
    """One placement as a :class:`~ada.topology.entities.TopoEquipment`.

    ``X/Y/Z`` are CELL-LOCAL (``GLOBAL_COORDS=False``): the compiler seats them at
    the deck's origin, see :func:`ada.topo_model.compile.equipment_space_offset`.
    ``LX/LY/LZ`` are stamped from the item even though the equipment definition
    doc would otherwise supply them — :func:`ada.topo_model.compile._equipment_to_object`
    requires all six coordinates up front and raises without them."""
    cog = item.cog if item.cog is not None else (0.0, 0.0, float(item.lz) / 2.0)
    return TopoEquipment(
        NAME=item.name,
        DESCRIPTION=item.type_slug,
        INCLUDE=True,
        SPACE_NAME=space_name,
        SPACE_LOC="FLOOR",
        GLOBAL_COORDS=False,
        X=round(float(x), 6),
        Y=round(float(y), 6),
        Z=round(float(z), 6),
        LX=float(item.lx),
        LY=float(item.ly),
        LZ=float(item.lz),
        ROT_Z=float(item.rot_z or 0.0),
        COGx=float(cog[0]),
        COGy=float(cog[1]),
        COGz=float(cog[2]),
        massDry=float(item.mass),
        massCont=0.0,
    )


# --------------------------------------------------------------------------- #
# Document integration
# --------------------------------------------------------------------------- #
def _merge_by_name(existing: list, generated: list[dict]) -> list[dict]:
    """Union two entity-dump lists on ``NAME``: a generated entry REPLACES the
    existing one of the same name in place (so document order is stable), and a
    new one is appended."""
    merged = [dict(row) if isinstance(row, dict) else row for row in existing]
    index = {row.get("NAME"): i for i, row in enumerate(merged) if isinstance(row, dict) and row.get("NAME")}
    for row in generated:
        pos = index.get(row.get("NAME"))
        if pos is None:
            index[row.get("NAME")] = len(merged)
            merged.append(row)
        else:
            merged[pos] = row
    return merged


def apply_layout(doc: dict, plan: LayoutPlan) -> dict:
    """Merge ``plan`` into a procedural document and return the result.

    The document is the compiler's / viewer's commit format (see
    :class:`ada.comms.rest.procedural.ProceduralDoc`); everything on it other
    than ``spaces``/``equipments`` — systems, openings, blueprint options, the
    engine header — is carried through untouched, so a layout can be applied to a
    document that already carries its process wiring. The input is not mutated.

    Merging is by ``NAME``: a deck or placement the plan generates replaces the
    document's entry of the same name and otherwise appends. Entries the plan
    does not name are left alone.
    """
    out = dict(doc or {})
    out["spaces"] = _merge_by_name(
        out.get("spaces") or [], [s.model_dump(mode="json", exclude_none=True) for s in plan.spaces]
    )
    out["equipments"] = _merge_by_name(
        out.get("equipments") or [], [e.model_dump(mode="json", exclude_none=True) for e in plan.placements]
    )
    return out


def validate_equipment_in_cells(doc: dict) -> list[str]:
    """Check the standing rule — every equipment sits in or on a cell — and
    return one message per violation (empty list = the document is sound).

    An equipment passes when its (rotation-aware) plan footprint lies fully
    inside some space's X/Y extents and its base Z lies within that space's
    height, ``ROOF``-seated units included ("on" the cell). Missing coordinates,
    an unknown ``SPACE_NAME`` and a footprint hanging off the deck are each
    reported by name. General-purpose: it reads a plain document, so it works on
    a hand-authored one as readily as on a :func:`plan_layout` result.
    """
    from .compile import equipment_space_offset

    spaces = [s if isinstance(s, TopoSpace) else TopoSpace(**_no_none(s)) for s in doc.get("spaces") or []]
    lookup: dict = {}
    for s in spaces:
        lookup[(s.STRUCTURE_NAME, s.NAME)] = s
        lookup.setdefault(s.NAME, s)

    problems: list[str] = []
    for raw in doc.get("equipments") or []:
        eq = raw if isinstance(raw, TopoEquipment) else TopoEquipment(**_no_none(raw))
        missing = [a for a in ("X", "Y", "Z", "LX", "LY", "LZ") if getattr(eq, a) is None]
        if missing:
            problems.append(f"{eq.NAME}: missing coordinates {missing}")
            continue
        space = lookup.get((eq.STRUCTURE_NAME, eq.SPACE_NAME)) or lookup.get(eq.SPACE_NAME)
        if space is None and not eq.GLOBAL_COORDS:
            problems.append(f"{eq.NAME}: SPACE_NAME {eq.SPACE_NAME!r} does not name a cell")
            continue
        ox, oy, oz = equipment_space_offset(eq, space)
        fx, fy = _footprint(LayoutItem(eq.NAME, "", eq.LX, eq.LY, eq.LZ, rot_z=eq.ROT_Z or 0.0))
        # The footprint centre is invariant under the rotation, so the rotated
        # box spans fx/fy about it.
        cx, cy = eq.X + ox + eq.LX / 2.0, eq.Y + oy + eq.LY / 2.0
        lo = (cx - fx / 2.0, cy - fy / 2.0, eq.Z + oz)
        hi = (cx + fx / 2.0, cy + fy / 2.0, eq.Z + oz + eq.LZ)
        if not any(_inside(lo, hi, s) for s in spaces):
            problems.append(
                f"{eq.NAME}: footprint [{lo[0]:.3f}, {hi[0]:.3f}] x [{lo[1]:.3f}, {hi[1]:.3f}] "
                f"at z={lo[2]:.3f} is not in or on any cell"
            )
    return problems


def _no_none(row) -> dict:
    """Drop ``None`` values before ``Topo*(**row)`` — a stored document may carry
    explicit nulls, which a field typed ``float`` with a ``None`` default accepts
    as a default but rejects as an argument (mirrors
    :func:`ada.topo_model.builder._strip_none`)."""
    return {k: v for k, v in row.items() if v is not None} if isinstance(row, dict) else row


def _inside(lo, hi, space: TopoSpace) -> bool:
    """Is the footprint ``lo``/``hi`` within ``space``'s plan extents, with its
    base at or between the space's floor and roof? (Base ON the roof counts — a
    ``SPACE_LOC="ROOF"`` unit sits on top of its cell.)"""
    tol = 1e-6
    return (
        space.X - tol <= lo[0]
        and hi[0] <= space.X + space.DX + tol
        and space.Y - tol <= lo[1]
        and hi[1] <= space.Y + space.DY + tol
        and space.Z - tol <= lo[2] <= space.Z + space.DZ + tol
    )
