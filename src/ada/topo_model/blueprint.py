"""SteelStru: a small, self-contained steel-structure blueprint.

This is the reference example for authoring a blueprint on top of the generic
``ada.topology`` engine. The engine turns a set of space boxes into a
:class:`~ada.topology.graph.CellGraph` whose classified faces and edges drive
the design:

- external floor faces  -> reinforced floors (plate + evenly spaced stringers)
- floor-face edges      -> girders (deduplicated where two cells share an edge)
- wall-face vertical edges -> columns (deduplicated the same way)

The profiles default to the same set as ``SimpleStru`` (IPE200 girders, HEB200
columns, 10 mm floor plate, HP140x8 stringers @ 0.4 m).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.config import logger
from ada.topology import BlueprintBase
from ada.topology.graph import GraphEdge, GraphFace

if TYPE_CHECKING:
    from ada.topology.entities import TopoOpening

__all__ = ["SteelStru"]

_MID_NDIGITS = 4
# Extend a negative-volume opening's cut past both plate faces so the hole
# punches cleanly through the plate thickness (metres).
_OPENING_CUT_MARGIN = 0.05

# How far a beam cut reaches past each wall face along the wall normal, so a door/
# window also severs the stiffener web that stands into the room (metres) — larger
# than any stiffener depth in use (HP140 => 0.14).
_OPENING_STIFFENER_CLEAR = 0.3

# Overshoot (metres) applied to a DETAIL-mode deck-edge notch so the strip clears
# both plate faces (along the thickness) and the plate corners (along the edge run),
# leaving a clean cut exactly at the girder top-flange inner edge.
_DECK_TRIM_MARGIN = 0.02


def _cut_crossing_secondary_beams(host: ada.Part, opening_name: str, lo: np.ndarray, hi: np.ndarray) -> int:
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
        try:
            bm.add_boolean(ada.PrimBox(f"{opening_name}_bcut_{count:02d}", tuple(lo), tuple(hi)))
            count += 1
        except Exception as exc:  # noqa: BLE001 - one bad beam cut must not sink the compile
            logger.warning("procedural: skipping opening %r beam cut in %r: %s", opening_name, bm.name, exc)
    return count


def _edge_midpoint_key(edge: GraphEdge) -> tuple[float, float, float]:
    p1, p2 = edge.get_points()[:2]
    mid = (np.asarray(p1, dtype=float) + np.asarray(p2, dtype=float)) / 2
    return tuple(round(float(v), _MID_NDIGITS) for v in mid)


def _face_center_key(face: GraphFace) -> tuple[float, float, float]:
    """A rounded face-centroid key so a cell's bounding face can be matched to
    the shared cell-graph wall it coincides with (``cell.faces`` and
    ``get_internal_walls()`` return distinct objects for the same physical wall)."""
    return tuple(round(float(v), _MID_NDIGITS) for v in face.get_centroid())


def _dedupe_edges(faces: list[GraphFace], horizontal: bool) -> list[GraphEdge]:
    """Collect the faces' edges with the requested orientation, keeping one edge
    per unique midpoint (adjacent cells contribute the same physical edge twice)."""
    unique: dict[tuple[float, float, float], GraphEdge] = {}
    for face in faces:
        for edge in face.edges:
            # is_horizontal() may hand back a numpy bool — compare by value
            if bool(edge.is_horizontal()) != horizontal:
                continue
            unique.setdefault(_edge_midpoint_key(edge), edge)
    return list(unique.values())


def _plate_world_aabb(pl: ada.Plate) -> tuple[np.ndarray, np.ndarray]:
    """World-space axis-aligned min/max corners of a plate (thickness included)."""
    bb = pl.bbox()
    return np.asarray(bb.p1, dtype=float), np.asarray(bb.p2, dtype=float)


def _opening_frame_axes(normal_axis: int) -> tuple[int, int]:
    """Given a plate's normal axis, return ``(width_axis, height_axis)`` for the
    reinforcement frame: the height runs along the vertical (global Z) for a wall
    and the width along the remaining in-plane (lateral) axis. A horizontal plate
    (normal along Z, i.e. a floor/roof) has no vertical in-plane axis, so the two
    in-plane axes are used in order."""
    in_plane = [a for a in range(3) if a != normal_axis]
    if 2 in in_plane:  # wall: keep height along the vertical axis
        height_axis = 2
        width_axis = next(a for a in in_plane if a != 2)
    else:  # floor/roof
        width_axis, height_axis = in_plane[0], in_plane[1]
    return width_axis, height_axis


def _build_reinforced_wall(
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


def _seat_below_deck(beam: ada.Beam, pl_thick: float) -> ada.Beam:
    """Drop a horizontal deck beam so the TOP of its section is flush with the TOP
    of the deck plate (top-of-steel = deck level), rather than straddling the deck
    line with its top flange sticking up above the plate.

    The shift uses beam eccentricity (``e1``/``e2``) so the end NODES stay on the
    deck edge — the column/girder joints don't move, only the swept profile drops.
    The rendered profile moves opposite to ``e`` (verified against the libtess2
    stream), so a positive z-eccentricity lowers the beam; the section top then
    lands at ``deck_z + pl_thick`` (the plate's top surface)."""
    off = beam.section.h / 2.0 - pl_thick
    beam.e1 = (0.0, 0.0, off)
    beam.e2 = (0.0, 0.0, off)
    return beam


def _build_reinforced_floor(
    name: str, points: list[ada.Point], pl_thick: float, stringer_sec: str, spacing: float
) -> ada.Part:
    """A reinforced floor built from a horizontal face outline: one plate plus
    stringer beams running along the longer plan direction, evenly distributed
    across the shorter one (edge positions carry girders, so they are skipped).
    Stringers are seated so their tops sit flush with the deck plate top."""
    plate = ada.Plate.from_3d_points(f"{name}_pl", points, pl_thick)

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
        _seat_below_deck(s, pl_thick)

    return ada.Part(name) / [plate, *stringers]


def _girder_flange_width(girder_sec: str) -> float:
    """Top-flange WIDTH of the I-girder section (metres). The section string is
    resolved by constructing a throwaway beam (OCC-free); an I-profile stores its
    top-flange width on ``w_top`` (e.g. IPE200 => 0.1)."""
    sec = ada.Beam("_probe", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), girder_sec).section
    return float(sec.w_top)


def _trim_deck_to_girders(plate: ada.Plate, points: list[ada.Point], pl_thick: float, girder_sec: str) -> None:
    """DETAIL mode: notch each of the deck plate's perimeter edges inboard by the
    girder top-flange half-width, so the plate spans the CLEAR opening between the
    surrounding I-girders' top flanges instead of overlapping them.

    Each bounding girder's axis sits on the cell edge and its top flange spans
    ``±w_top/2`` about that axis; the deck plate should recede to the inboard flange
    edge (``axis + w_top/2``). For every perimeter edge we subtract an axis-aligned
    strip that runs the full edge length, spans the plate thickness (plus a small
    margin so it clears both faces), and reaches ``w_top/2`` inboard from the edge.

    OCC-free: each strip is removed with a :class:`~ada.PrimBox` boolean, which
    folds into the plate in the libtess2/NGEOM stream (same mechanism as the
    opening cuts). A cut that fails is logged and skipped."""
    setback = _girder_flange_width(girder_sec) / 2.0
    if setback <= 0.0:
        return

    pl_lo, pl_hi = _plate_world_aabb(plate)
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

        try:
            plate.add_boolean(ada.PrimBox(f"{plate.name}_trim_{i:02d}", tuple(cut_lo), tuple(cut_hi)))
        except Exception as exc:  # noqa: BLE001 - one bad deck notch must not sink the compile
            logger.warning("procedural: skipping deck trim %r edge %d: %s", plate.name, i, exc)


class SteelStru(BlueprintBase):
    """Generic steel structure: reinforced floors, girders and columns derived
    purely from the cell graph's classified faces/edges."""

    def __init__(
        self,
        name: str = "SteelStru",
        girder_sec: str = "IPE200",
        column_sec: str = "HEB200",
        stringer_sec: str = "HP140x8",
        pl_thick: float = 10e-3,
        stringer_spacing: float = 0.4,
        reinforce_internal_walls: bool = False,
        reinforce_external_walls: bool = False,
        enclosed_cells: list[str] | None = None,
        wall_pl_thick: float = 8e-3,
        detail: bool = False,
    ):
        super().__init__()
        self.name = name
        # Detail level of the build: when True, later phases trim deck plate edges
        # to the girder top-flange outline and model I-girder joints. Phase 1 keeps
        # the geometry identical to the simulation model (flag threaded, unused).
        self.detail = detail
        self.girder_sec = girder_sec
        self.column_sec = column_sec
        self.stringer_sec = stringer_sec
        self.pl_thick = pl_thick
        self.stringer_spacing = stringer_spacing
        self.reinforce_internal_walls = reinforce_internal_walls
        # Reinforce the outer (unshared) vertical faces too — combined with the
        # already-reinforced external floor/roof decks this fully encloses a room
        # in plated, stiffened walls.
        self.reinforce_external_walls = reinforce_external_walls
        # Per-cell enclosure: cells named here get EVERY bounding face plated
        # (all four walls + the floor/roof decks, including the shared internal
        # ones), so exactly those rooms are fully enclosed — the rest stay open
        # steel frame. Wins over the global reinforce_* flags for those cells.
        self.enclosed_cells = list(enclosed_cells or [])
        self.wall_pl_thick = wall_pl_thick

    def _group_prefix(self) -> str:
        return self.name

    def _inward(self, face: GraphFace) -> tuple[float, float, float]:
        """Unit-ish vector from a face centre toward its cell's centre — the
        'into the room' direction used to orient wall stiffeners inward."""
        c = np.asarray(face.parent_cell.get_centroid(), dtype=float)
        f = np.asarray(face.get_centroid(), dtype=float)
        v = c - f
        n = float(np.linalg.norm(v))
        return tuple(v / n) if n > 1e-9 else (0.0, 0.0, 1.0)

    def _wall(self, name: str, face: GraphFace) -> ada.Part:
        wall = _build_reinforced_wall(
            name, face.get_points(), self.wall_pl_thick, self.stringer_sec, self.stringer_spacing, self._inward(face)
        )
        # penetration blueprints reach the built wall through the face
        face.associated_part = wall
        return wall

    def _detail_trim_deck(self, floor: ada.Part, points: list[ada.Point]) -> None:
        """In DETAIL mode, trim the deck plate(s) inside a built floor part back to
        the surrounding girders' top-flange inner edges. A no-op in simulation mode
        (``self.detail is False``), keeping that geometry byte-identical to before."""
        if not self.detail:
            return
        for pl in floor.get_all_physical_objects(by_type=ada.Plate):
            _trim_deck_to_girders(pl, points, self.pl_thick, self.girder_sec)

    def build(self) -> ada.Part:
        self.output_part = ada.Part(self.name)
        cg = self.builder.cell_graph

        floor_faces = cg.get_external_floors()
        # Shared horizontal faces between vertically-stacked cells are *internal*
        # floors — the deck one storey walks on. They must be built too, else the
        # floor between two open (non-enclosed) stacked cells is simply missing
        # (only an enclosed cell's decks were built before). One face per shared
        # plane (get_internal_floors dedupes the up/down pair).
        internal_floors = cg.get_internal_floors()
        internal_walls = cg.get_internal_walls()
        external_walls = cg.get_external_walls()
        enclosed = set(self.enclosed_cells)

        # Nest the compiled output per cell so the viewer's selection tree groups
        # a room's decks + walls under one "Room_<cell>" part; shared,
        # edge-derived steel (girders/columns) goes under a single "Frame".
        rooms: dict[str, ada.Part] = {}

        def room(cell_name: str) -> ada.Part:
            if cell_name not in rooms:
                rooms[cell_name] = ada.Part(f"Room_{cell_name}")
            return rooms[cell_name]

        # External floor/roof decks, grouped by the cell they belong to. Every built
        # deck is recorded by face guid so the deck faces can be tagged with their
        # plate afterwards (like walls are) — a routed riser crossing a deck then
        # gets an automatic cutout + penetration detail (see _tag_built_floors).
        built_floor_guids: set[str] = set()
        built_floor_by_guid: dict[str, ada.Part] = {}
        for i, face in enumerate(floor_faces):
            built_floor_guids.add(face.guid)
            floor = _build_reinforced_floor(
                f"Floor_{i:02d}", face.get_points(), self.pl_thick, self.stringer_sec, self.stringer_spacing
            )
            built_floor_by_guid[face.guid] = floor
            self._detail_trim_deck(floor, face.get_points())
            room(face.parent_cell.name).add_part(floor)

        # Internal (shared) decks between stacked cells. Skip any face an enclosed
        # cell will plate below (guard by guid) so a deck is never built twice.
        for i, face in enumerate(internal_floors):
            if face.guid in built_floor_guids:
                continue
            built_floor_guids.add(face.guid)
            deck = _build_reinforced_floor(
                f"IntFloor_{i:02d}", face.get_points(), self.pl_thick, self.stringer_sec, self.stringer_spacing
            )
            built_floor_by_guid[face.guid] = deck
            self._detail_trim_deck(deck, face.get_points())
            room(face.parent_cell.name).add_part(deck)

        # Fully enclosed rooms: plate every bounding face of the flagged cells —
        # all four walls (external + shared internal) plus any deck face not
        # already built (e.g. the internal deck under a second-floor room).
        cell_by_name = {f.parent_cell.name: f.parent_cell for f in (*floor_faces, *internal_walls, *external_walls)}
        # Map a shared internal wall to its member object so a plated enclosed wall
        # can tag it (so penetration modelling — which walks get_internal_walls() —
        # only ever cuts through walls that were actually built).
        iw_by_key = {_face_center_key(w): w for w in internal_walls}
        for cname in enclosed:
            cell = cell_by_name.get(cname)
            if cell is None:
                continue
            for j, face in enumerate(cell.faces):
                if face.is_horizontal():
                    if face.guid in built_floor_guids:
                        continue
                    built_floor_guids.add(face.guid)
                    deck = _build_reinforced_floor(
                        f"Deck_{cname}_{j:02d}",
                        face.get_points(),
                        self.pl_thick,
                        self.stringer_sec,
                        self.stringer_spacing,
                    )
                    built_floor_by_guid[face.guid] = deck
                    self._detail_trim_deck(deck, face.get_points())
                    room(cname).add_part(deck)
                else:
                    wall = self._wall(f"Wall_{cname}_{j:02d}", face)
                    # If this bounding wall is a shared internal wall, tag the
                    # member the penetration engine sees with the built part.
                    member = iw_by_key.get(_face_center_key(face))
                    if member is not None:
                        member.associated_part = wall
                    room(cname).add_part(wall)

        # Tag the deck faces the penetration engine walks (get_external_floors /
        # get_internal_floors return the canonical face objects) with the plate that
        # was actually built for them — keyed by guid so it works no matter which
        # pass built the deck. A run crossing a tagged deck now cuts a real hole.
        for face in cg.get_external_floors() + cg.get_internal_floors():
            built = built_floor_by_guid.get(face.guid)
            if built is not None:
                face.associated_part = built

        # Global wall reinforcement (kept for back-compat) — skips cells already
        # fully plated by the enclosure pass so a wall is never built twice.
        if self.reinforce_internal_walls:
            for i, face in enumerate(internal_walls):
                if face.parent_cell.name in enclosed:
                    continue
                room(face.parent_cell.name).add_part(self._wall(f"Wall_{i:02d}", face))
        if self.reinforce_external_walls:
            for i, face in enumerate(external_walls):
                if face.parent_cell.name in enclosed:
                    continue
                room(face.parent_cell.name).add_part(self._wall(f"ExtWall_{i:02d}", face))

        # Shared steel frame: girders (floor-edge) + columns (wall-edge). Internal
        # decks contribute their perimeter girders too, at the intermediate
        # elevation (deduped against the external-floor edges by midpoint).
        # Girders carry the deck edges; seat each so its top flange is flush with
        # the deck plate top (top-of-steel = deck), not straddling the deck line.
        girders = [
            _seat_below_deck(ada.Beam(f"Girder_{i:02d}", *edge.get_points()[:2], self.girder_sec), self.pl_thick)
            for i, edge in enumerate(_dedupe_edges(floor_faces + internal_floors, horizontal=True))
        ]
        columns = [
            ada.Beam(f"Column_{i:02d}", *edge.get_points()[:2], self.column_sec)
            for i, edge in enumerate(_dedupe_edges(external_walls + internal_walls, horizontal=False))
        ]
        frame = ada.Part("Frame")
        frame.add_part(ada.Part("Girders") / girders)
        frame.add_part(ada.Part("Columns") / columns)

        for r in rooms.values():
            self.output_part.add_part(r)
        self.output_part.add_part(frame)
        return self.output_part

    def cut_opening(self, host: ada.Part, opening: TopoOpening) -> ada.Part | None:
        """Cut a negative-volume opening into every built plate it overlaps and
        return its reinforcement framing (or ``None`` when it overlaps no plate).

        The opening's placed box subtracts from each overlapping plate; a ``door``
        subtype extends the cut down to the wall's floor (full-height opening),
        while a ``window`` keeps its punched rectangle at the placed Z. Around the
        hole in the dominant wall plate the reinforcement uses the same stud/rail
        section as the wall stiffeners (``self.stringer_sec``):

        - **door**   : jamb studs both sides + a head/lintel beam + a threshold
          (sill at floor level).
        - **window** : jamb studs both sides + a head beam + a sill beam.

        A boolean cut that fails is logged and skipped so one bad opening never
        sinks the whole compile."""
        p1 = np.asarray(tuple(opening.get_p1()), dtype=float)
        p2 = np.asarray(tuple(opening.get_p2()), dtype=float)
        box_lo, box_hi = np.minimum(p1, p2), np.maximum(p1, p2)
        subtype = getattr(opening, "SUBTYPE", "door")

        host_hole: tuple[int, np.ndarray, np.ndarray, ada.Plate] | None = None
        best_overlap = 0.0
        cut_any = False
        for pl in host.get_all_physical_objects(by_type=ada.Plate):
            pl_lo, pl_hi = _plate_world_aabb(pl)
            ov_lo, ov_hi = np.maximum(box_lo, pl_lo), np.minimum(box_hi, pl_hi)
            if np.any(ov_hi - ov_lo <= 1e-9):
                continue  # no genuine overlap with this plate

            normal_axis = int(np.argmin(pl_hi - pl_lo))  # plate's thin (through) axis
            is_wall = normal_axis in (0, 1)

            # In-plane extents come from the opening (clamped to the plate face);
            # the cut spans fully through the plate along its normal.
            cut_lo, cut_hi = box_lo.copy(), box_hi.copy()
            cut_lo[normal_axis] = pl_lo[normal_axis] - _OPENING_CUT_MARGIN
            cut_hi[normal_axis] = pl_hi[normal_axis] + _OPENING_CUT_MARGIN
            for a in range(3):
                if a == normal_axis:
                    continue
                cut_lo[a] = max(cut_lo[a], pl_lo[a])
                cut_hi[a] = min(cut_hi[a], pl_hi[a])
            if is_wall and subtype == "door":
                cut_lo[2] = pl_lo[2] - _OPENING_CUT_MARGIN  # door reaches the floor

            try:
                pl.add_boolean(ada.PrimBox(f"{opening.NAME}_cut", tuple(cut_lo), tuple(cut_hi)))
            except Exception as exc:  # noqa: BLE001 - last-resort per-opening guard
                logger.warning("procedural: skipping opening %r cut in %r: %s", opening.NAME, pl.name, exc)
                continue
            cut_any = True

            vol = float(np.prod(ov_hi - ov_lo))
            if is_wall and vol > best_overlap:
                best_overlap = vol
                host_hole = (normal_axis, cut_lo.copy(), cut_hi.copy(), pl)

        if not cut_any or host_hole is None:
            return None

        # A cut plate still leaves the wall stiffeners (and deck stringers) barring
        # the hole. Cut every secondary member crossing the opening so a door/window
        # is a clear void; the box is extended along the wall normal to reach the
        # stiffener web that stands into the room. Primary girders/columns are left
        # intact (they carry load and merely border the opening).
        h_normal_axis, h_cut_lo, h_cut_hi, h_plate = host_hole
        pl_lo, pl_hi = _plate_world_aabb(h_plate)
        bcut_lo, bcut_hi = h_cut_lo.copy(), h_cut_hi.copy()
        bcut_lo[h_normal_axis] = pl_lo[h_normal_axis] - _OPENING_STIFFENER_CLEAR
        bcut_hi[h_normal_axis] = pl_hi[h_normal_axis] + _OPENING_STIFFENER_CLEAR
        _cut_crossing_secondary_beams(host, opening.NAME, bcut_lo, bcut_hi)

        return self._opening_reinforcement(opening, subtype, *host_hole)

    def _opening_reinforcement(
        self,
        opening: TopoOpening,
        subtype: str,
        normal_axis: int,
        cut_lo: np.ndarray,
        cut_hi: np.ndarray,
        plate: ada.Plate,
    ) -> ada.Part:
        """Frame the hole in ``plate`` with jamb studs + head + sill/threshold
        beams (per ``subtype``), all in ``self.stringer_sec`` and standing
        perpendicular to the plate plane (local up along the plate normal)."""
        width_axis, height_axis = _opening_frame_axes(normal_axis)
        pl_lo, pl_hi = _plate_world_aabb(plate)
        ncoord = (pl_lo[normal_axis] + pl_hi[normal_axis]) / 2.0

        # Clamp the hole extents to the plate face so the frame sits on the plate
        # (e.g. a door threshold lands exactly on the floor, not the cut margin).
        w_lo = max(float(cut_lo[width_axis]), float(pl_lo[width_axis]))
        w_hi = min(float(cut_hi[width_axis]), float(pl_hi[width_axis]))
        h_lo = max(float(cut_lo[height_axis]), float(pl_lo[height_axis]))
        h_hi = min(float(cut_hi[height_axis]), float(pl_hi[height_axis]))

        up = [0.0, 0.0, 0.0]
        up[normal_axis] = 1.0
        up = tuple(up)

        def pt(w: float, h: float) -> tuple[float, float, float]:
            q = [0.0, 0.0, 0.0]
            q[normal_axis] = ncoord
            q[width_axis] = w
            q[height_axis] = h
            return tuple(q)

        sec = self.stringer_sec
        base = f"Opening_{opening.NAME}"
        head_name, sill_name = ("lintel", "threshold") if subtype == "door" else ("head", "sill")
        beams = [
            ada.Beam(f"{base}_jamb_L", pt(w_lo, h_lo), pt(w_lo, h_hi), sec, up=up),
            ada.Beam(f"{base}_jamb_R", pt(w_hi, h_lo), pt(w_hi, h_hi), sec, up=up),
            ada.Beam(f"{base}_{head_name}", pt(w_lo, h_hi), pt(w_hi, h_hi), sec, up=up),
            ada.Beam(f"{base}_{sill_name}", pt(w_lo, h_lo), pt(w_hi, h_lo), sec, up=up),
        ]
        return ada.Part(base) / beams
