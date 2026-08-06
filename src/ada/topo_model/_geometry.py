"""Low-level geometry plumbing shared by the SteelStru blueprint.

These are the grid/geometry primitives the blueprint narrative leans on — plate
outlines, beam seating, edge dedup, and the axis-aligned boolean-cut used by
every hole/notch — factored out of :mod:`ada.topo_model.blueprint` so the
blueprint reads as orchestration. All OCC-free: cuts are :class:`ada.PrimBox`
booleans that fold into the plate/beam in the libtess2/NGEOM stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import ada
from ada.config import logger
from ada.topology.graph import GraphEdge, GraphFace

__all__ = [
    "DeckLedger",
    "cut_box",
    "dedupe_edges",
    "deck_plate",
    "edge_midpoint_key",
    "face_center_key",
    "frame_axes",
    "girder_flange_width",
    "plate_world_aabb",
    "profile_top_offset",
    "reinforced_floor",
    "reinforced_wall",
    "rounded_point_key",
    "seat_deck_beam",
    "trim_deck_to_girders",
    "cut_crossing_secondary_beams",
]

_KEY_NDIGITS = 4
# Overshoot (metres) applied to a DETAIL-mode deck-edge notch so the strip clears
# both plate faces (along the thickness) and the plate corners (along the edge run),
# leaving a clean cut exactly at the girder top-flange inner edge.
_DECK_TRIM_MARGIN = 0.02


# --------------------------------------------------------------------------- #
# Boolean cut — the one axis-aligned-box subtraction every hole/notch reuses
# --------------------------------------------------------------------------- #
def cut_box(target, name: str, lo, hi) -> Exception | None:
    """Subtract the axis-aligned box ``[lo, hi]`` from ``target`` with a
    :class:`ada.PrimBox` boolean (the OCC-free cut that folds into the plate/beam
    in the libtess2/NGEOM stream). Returns ``None`` on success or the caught
    exception on failure, so a caller can log its own context and skip — one bad
    cut must never sink the whole compile."""
    try:
        target.add_boolean(ada.PrimBox(name, tuple(lo), tuple(hi)))
        return None
    except Exception as exc:  # noqa: BLE001 - one bad boolean cut must not sink the compile
        return exc


# --------------------------------------------------------------------------- #
# Rounded coincidence keys (match distinct face/edge objects that coincide)
# --------------------------------------------------------------------------- #
def rounded_point_key(vec, ndigits: int = _KEY_NDIGITS) -> tuple[float, float, float]:
    """A rounded ``(x, y, z)`` key so two distinct objects that occupy the same
    physical location hash together (the cell graph hands back separate face/edge
    objects for the same shared wall/edge)."""
    return tuple(round(float(v), ndigits) for v in vec)


def edge_midpoint_key(edge: GraphEdge) -> tuple[float, float, float]:
    p1, p2 = edge.get_points()[:2]
    mid = (np.asarray(p1, dtype=float) + np.asarray(p2, dtype=float)) / 2
    return rounded_point_key(mid)


def face_center_key(face: GraphFace) -> tuple[float, float, float]:
    """A rounded face-centroid key so a cell's bounding face can be matched to
    the shared cell-graph wall it coincides with (``cell.faces`` and
    ``get_internal_walls()`` return distinct objects for the same physical wall)."""
    return rounded_point_key(face.get_centroid())


def dedupe_edges(faces: list[GraphFace], horizontal: bool) -> list[GraphEdge]:
    """Collect the faces' edges with the requested orientation, keeping one edge
    per unique midpoint (adjacent cells contribute the same physical edge twice)."""
    unique: dict[tuple[float, float, float], GraphEdge] = {}
    for face in faces:
        for edge in face.edges:
            # is_horizontal() may hand back a numpy bool — compare by value
            if bool(edge.is_horizontal()) != horizontal:
                continue
            unique.setdefault(edge_midpoint_key(edge), edge)
    return list(unique.values())


# --------------------------------------------------------------------------- #
# Plate / frame geometry
# --------------------------------------------------------------------------- #
def plate_world_aabb(pl: ada.Plate) -> tuple[np.ndarray, np.ndarray]:
    """World-space axis-aligned min/max corners of a plate (thickness included)."""
    bb = pl.bbox()
    return np.asarray(bb.p1, dtype=float), np.asarray(bb.p2, dtype=float)


def frame_axes(normal_axis: int, feed_axis: int | None = None) -> tuple[int, int]:
    """Given a plate/face's normal axis, return ``(width_axis, height_axis)`` for a
    rectangular frame or opening: the height runs along the vertical (global Z) for
    a wall and the width along the remaining in-plane (lateral) axis.

    A horizontal plate (normal along Z, i.e. a floor/roof) has no vertical in-plane
    axis. When the run's travel is known (``feed_axis`` — the feeding horizontal
    leg on a riser through a deck) the HEIGHT/opening rotates onto that travel axis
    and the WIDTH takes the perpendicular horizontal axis; otherwise the two
    in-plane axes are used in order."""
    in_plane = [a for a in range(3) if a != normal_axis]
    if 2 in in_plane:  # wall: keep height along the vertical axis
        height_axis = 2
        width_axis = next(a for a in in_plane if a != 2)
    elif feed_axis is not None and feed_axis in in_plane:  # deck: orient from travel
        height_axis = feed_axis
        width_axis = next(a for a in in_plane if a != feed_axis)
    else:  # floor/roof with no known travel: in-order fallback
        width_axis, height_axis = in_plane[0], in_plane[1]
    return width_axis, height_axis


def girder_flange_width(girder_sec: str) -> float:
    """Top-flange WIDTH of the I-girder section (metres). The section string is
    resolved by constructing a throwaway beam (OCC-free); an I-profile stores its
    top-flange width on ``w_top`` (e.g. IPE200 => 0.1)."""
    sec = ada.Beam("_probe", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), girder_sec).section
    return float(sec.w_top)


# --------------------------------------------------------------------------- #
# Beam seating (seat a section's TOP at a target elevation via eccentricity)
# --------------------------------------------------------------------------- #
def profile_top_offset(beam: ada.Beam) -> float:
    """How far the beam's section reaches ABOVE its axis, in the local up direction
    (metres). Read from the section's outer profile so it is correct for ANY
    section, not just symmetric ones: a centred I (IPE200) reaches ``h/2`` up, but
    a bulb flat (HP140x8) is referenced at its top so it reaches ``0`` up and hangs
    fully below the axis. A horizontal deck beam keeps the default up (+Z), so the
    local +y extent maps to +Z."""
    curve = beam.section.get_section_profile().outer_curve
    pts = getattr(curve, "points2d", None) or curve.points
    return max(float(p[1]) for p in pts)


def seat_deck_beam(beam: ada.Beam, top_z: float) -> ada.Beam:
    """Seat a horizontal deck beam so the TOP of its section lands at ``top_z``,
    via beam eccentricity (the end NODES stay put — column joints don't move, only
    the swept profile shifts). The rendered profile moves opposite to ``e`` (verified
    against the libtess2 stream), and reaches ``profile_top_offset`` above the axis,
    so ``e_z = profile_top_offset - (top_z - node_z)`` puts the section top exactly on
    ``top_z`` for any section (centred or top-referenced)."""
    node_z = float(beam.n1.p[2])
    e_z = profile_top_offset(beam) - (top_z - node_z)
    beam.e1 = (0.0, 0.0, e_z)
    beam.e2 = (0.0, 0.0, e_z)
    return beam


def deck_plate(name: str, points: list[ada.Point], pl_thick: float) -> ada.Plate:
    """A deck plate whose TOP sits at the outline elevation (the deck line), so the
    walking surface is at the grid level and the steel is below it — consistently,
    regardless of the face outline's winding. ``Plate.from_3d_points`` extrudes along
    the outline normal; when that points up the plate would sit ABOVE the deck line,
    so we flip it to extrude DOWN. Both faces of a deck at the same elevation then
    agree (an internal floor and an enclosing-cell deck no longer disagree by the
    plate thickness)."""
    plate = ada.Plate.from_3d_points(name, points, pl_thick)
    if float(plate.poly.normal[2]) > 0:
        plate = ada.Plate.from_3d_points(name, points, pl_thick, flip_normal=True)
    return plate


# --------------------------------------------------------------------------- #
# Reinforced deck / wall parts (plate + evenly spaced stringers / stiffeners)
# --------------------------------------------------------------------------- #
def reinforced_floor(
    name: str, points: list[ada.Point], pl_thick: float, stringer_sec: str, spacing: float
) -> ada.Part:
    """A reinforced floor built from a horizontal face outline: one plate (top at the
    deck line) plus stringer beams running along the longer plan direction, evenly
    distributed across the shorter one (edge positions carry girders, so they are
    skipped). Stringers hang under the plate — their tops seat at the plate bottom."""
    plate = deck_plate(f"{name}_pl", points, pl_thick)

    pts = np.asarray([tuple(p) for p in points], dtype=float)
    z = float(pts[:, 2].mean())
    (x0, y0), (x1, y1) = pts[:, :2].min(axis=0), pts[:, :2].max(axis=0)

    tol = spacing * 1e-3
    stringers = []
    if (x1 - x0) >= (y1 - y0):
        for i, y in enumerate(np.arange(y0 + spacing, y1 - tol, spacing)):
            stringers.append(ada.Beam(f"{name}_str_{i:02d}", (x0, y, z), (x1, y, z), stringer_sec))
    else:
        for i, x in enumerate(np.arange(x0 + spacing, x1 - tol, spacing)):
            stringers.append(ada.Beam(f"{name}_str_{i:02d}", (x, y0, z), (x, y1, z), stringer_sec))
    for s in stringers:
        seat_deck_beam(s, z - pl_thick)  # stringer top attaches to the plate underside

    return ada.Part(name) / [plate, *stringers]


def reinforced_wall(
    name: str,
    points: list[ada.Point],
    pl_thick: float,
    stiffener_sec: str,
    spacing: float,
    inward: tuple[float, float, float] | None = None,
) -> ada.Part:
    """A reinforced wall from a vertical face outline: one plate plus vertical
    stiffener beams evenly distributed along the wall's horizontal run. The
    stiffeners' local up vector is the wall normal so the profile stands
    perpendicular to (not flat in) the plate plane; ``inward`` (a vector pointing
    into the room) signs it so the stiffener webs stand inward, into the
    enclosure, rather than out of it."""
    plate = ada.Plate.from_3d_points(f"{name}_pl", points, pl_thick)

    pts = np.asarray([tuple(p) for p in points], dtype=float)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    normal_axis = int(np.argmax(hi - lo == 0.0)) if np.any(hi - lo == 0.0) else int(np.argmin(hi - lo))
    run_axis = next(a for a in (0, 1) if a != normal_axis)  # horizontal in-plane axis
    z0, z1 = lo[2], hi[2]

    up = [0.0, 0.0, 0.0]
    # The stiffener profile's web/material grows on the -up side of the beam (its
    # outline sits in local -y), so to stand the web INTO the room we point up OUT
    # of it — opposite the inward vector along the wall normal. Without an inward
    # hint, default to +normal (web on the -normal side).
    if inward is not None and abs(inward[normal_axis]) > 1e-9:
        up[normal_axis] = -1.0 if inward[normal_axis] > 0 else 1.0
    else:
        up[normal_axis] = 1.0

    tol = spacing * 1e-3
    stiffeners = []
    for i, s in enumerate(np.arange(lo[run_axis] + spacing, hi[run_axis] - tol, spacing)):
        p1 = [lo[0], lo[1], z0]
        p2 = [lo[0], lo[1], z1]
        p1[run_axis] = p2[run_axis] = s
        stiffeners.append(ada.Beam(f"{name}_stf_{i:02d}", tuple(p1), tuple(p2), stiffener_sec, up=tuple(up)))

    return ada.Part(name) / [plate, *stiffeners]


# --------------------------------------------------------------------------- #
# DETAIL-mode cuts (deck-edge trim + secondary-member severing)
# --------------------------------------------------------------------------- #
def trim_deck_to_girders(plate: ada.Plate, points: list[ada.Point], pl_thick: float, girder_sec: str) -> None:
    """DETAIL mode: notch each of the deck plate's perimeter edges inboard by the
    girder top-flange half-width, so the plate spans the CLEAR opening between the
    surrounding I-girders' top flanges instead of overlapping them.

    Each bounding girder's axis sits on the cell edge and its top flange spans
    ``±w_top/2`` about that axis; the deck plate should recede to the inboard flange
    edge (``axis + w_top/2``). For every perimeter edge we subtract an axis-aligned
    strip that runs the full edge length, spans the plate thickness (plus a small
    margin so it clears both faces), and reaches ``w_top/2`` inboard from the edge.
    A cut that fails is logged and skipped (see :func:`cut_box`)."""
    setback = girder_flange_width(girder_sec) / 2.0
    if setback <= 0.0:
        return

    pl_lo, pl_hi = plate_world_aabb(plate)
    pts = np.asarray([tuple(p) for p in points], dtype=float)
    normal_axis = int(np.argmin(pl_hi - pl_lo))  # deck through-thickness axis (global Z)
    in_plane = [a for a in range(3) if a != normal_axis]
    center = (pl_lo + pl_hi) / 2.0

    n = len(pts)
    for i in range(n):
        p_a, p_b = pts[i], pts[(i + 1) % n]
        edge = p_b - p_a
        # in-plane axis the edge runs along, and the perpendicular in-plane axis the
        # notch recedes along (rectangular, axis-aligned decks => both are axes).
        edge_axis = max(in_plane, key=lambda a: abs(edge[a]))
        notch_axis = next(a for a in in_plane if a != edge_axis)
        c = float(p_a[notch_axis])  # the edge coordinate (== plate boundary here)
        inward = 1.0 if center[notch_axis] > c else -1.0

        cut_lo, cut_hi = pl_lo.copy(), pl_hi.copy()
        # full through-thickness (+ margin) so the strip clears both plate faces
        cut_lo[normal_axis] = pl_lo[normal_axis] - _DECK_TRIM_MARGIN
        cut_hi[normal_axis] = pl_hi[normal_axis] + _DECK_TRIM_MARGIN
        # full run along the edge (+ margin) so the plate corners recede too
        cut_lo[edge_axis] = pl_lo[edge_axis] - _DECK_TRIM_MARGIN
        cut_hi[edge_axis] = pl_hi[edge_axis] + _DECK_TRIM_MARGIN
        # inboard strip of width `setback` from the boundary edge toward the centre
        if inward > 0:
            cut_lo[notch_axis] = c - _DECK_TRIM_MARGIN
            cut_hi[notch_axis] = c + setback
        else:
            cut_lo[notch_axis] = c - setback
            cut_hi[notch_axis] = c + _DECK_TRIM_MARGIN

        exc = cut_box(plate, f"{plate.name}_trim_{i:02d}", cut_lo, cut_hi)
        if exc is not None:
            logger.warning("procedural: skipping deck trim %r edge %d: %s", plate.name, i, exc)


def cut_crossing_secondary_beams(host: ada.Part, opening_name: str, lo: np.ndarray, hi: np.ndarray) -> int:
    """Boolean-cut every SECONDARY member (wall stiffener / deck stringer, matched
    by the ``_stf_``/``_str_`` name marker) whose axis segment overlaps the box
    ``[lo, hi]`` — the studs/stringers that would otherwise bar an opening. Primary
    girders/columns are deliberately left intact. Returns the number cut."""
    count = 0
    for bm in host.get_all_physical_objects(by_type=ada.Beam):
        if "_stf_" not in bm.name and "_str_" not in bm.name:
            continue
        p1 = np.asarray([float(c) for c in bm.n1.p], dtype=float)
        p2 = np.asarray([float(c) for c in bm.n2.p], dtype=float)
        b_lo, b_hi = np.minimum(p1, p2) - 1e-6, np.maximum(p1, p2) + 1e-6
        if np.any(b_hi < lo) or np.any(b_lo > hi):
            continue  # axis segment doesn't reach the opening box
        exc = cut_box(bm, f"{opening_name}_bcut_{count:02d}", lo, hi)
        if exc is None:
            count += 1
        else:
            logger.warning("procedural: skipping opening %r beam cut in %r: %s", opening_name, bm.name, exc)
    return count


# --------------------------------------------------------------------------- #
# Deck-build bookkeeping (dedup a shared deck plane across the three build passes)
# --------------------------------------------------------------------------- #
@dataclass
class DeckLedger:
    """Tracks the decks SteelStru.build has already plated so a shared deck plane is
    never built twice. Dedup is by PLANE (rounded face centroid) as well as guid:
    the SAME physical deck is a distinct face object with a distinct guid depending
    on which cell it is reached through (an internal-floor face vs an enclosing
    cell's bottom face), so a guid-only guard would plate the shared plane twice."""

    guids: set[str] = field(default_factory=set)
    planes: set[tuple[float, float, float]] = field(default_factory=set)
    by_guid: dict[str, ada.Part] = field(default_factory=dict)

    def already_built(self, face: GraphFace) -> bool:
        return face.guid in self.guids or face_center_key(face) in self.planes

    def record(self, face: GraphFace, deck: ada.Part) -> None:
        self.guids.add(face.guid)
        self.planes.add(face_center_key(face))
        self.by_guid[face.guid] = deck
