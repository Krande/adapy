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

The low-level geometry plumbing (plate outlines, beam seating, edge dedup, the
axis-aligned boolean cut every hole/notch reuses) lives in
:mod:`ada.topo_model._geometry`, so the blueprint below reads as orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.config import logger
from ada.topology import BlueprintBase
from ada.topology.graph import GraphFace

# Re-export for callers/tests that reach the wall builder through this module.
from ._geometry import (
    DeckLedger,
    cut_box,
    cut_crossing_secondary_beams,
    dedupe_edges,
    face_center_key,
    frame_axes,
    girder_flange_width,
    plate_world_aabb,
    reinforced_floor,
    reinforced_wall,
    seat_deck_beam,
)

# Re-export the wall builder under its historical private name for tests that
# import it from this module (tests/core/topo_model/test_steel_stru.py).
_build_reinforced_wall = reinforced_wall

if TYPE_CHECKING:
    from ada.topology.entities import TopoOpening

__all__ = ["SteelStru"]

# Extend a negative-volume opening's cut past both plate faces so the hole
# punches cleanly through the plate thickness (metres).
_OPENING_CUT_MARGIN = 0.05

# How far a beam cut reaches past each wall face along the wall normal, so a door/
# window also severs the stiffener web that stands into the room (metres) — larger
# than any stiffener depth in use (HP140 => 0.14).
_OPENING_STIFFENER_CLEAR = 0.3


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
        # to the girder top-flange outline. When this blueprint is owned by a
        # ProceduralBuilder the LOD lives on the root (read via the ``detail``
        # property below); this constructor flag is the standalone fallback.
        self._detail = detail
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

    @property
    def detail(self) -> bool:
        """Detail level of the build. Prefers the owning
        :class:`~ada.topo_model.builder.ProceduralBuilder` (``self.procedural``)
        when one is attached, so the LOD has a single home on the root; falls
        back to the constructor flag when the blueprint is built standalone."""
        procedural = getattr(self, "procedural", None)
        if procedural is not None:
            return procedural.detail
        return self._detail

    def _inward(self, face: GraphFace) -> tuple[float, float, float]:
        """Unit-ish vector from a face centre toward its cell's centre — the
        'into the room' direction used to orient wall stiffeners inward."""
        c = np.asarray(face.parent_cell.get_centroid(), dtype=float)
        f = np.asarray(face.get_centroid(), dtype=float)
        v = c - f
        n = float(np.linalg.norm(v))
        return tuple(v / n) if n > 1e-9 else (0.0, 0.0, 1.0)

    def _wall(self, name: str, face: GraphFace) -> ada.Part:
        wall = reinforced_wall(
            name, face.get_points(), self.wall_pl_thick, self.stringer_sec, self.stringer_spacing, self._inward(face)
        )
        # penetration blueprints reach the built wall through the face
        face.associated_part = wall
        return wall

    def _floor(self, name: str, face: GraphFace, ledger: DeckLedger, room: ada.Part) -> None:
        """Build a reinforced deck for ``face``, record it in ``ledger`` (so a
        shared plane is never plated twice and the deck can later be tagged onto
        the face for penetration cutting), and add it to ``room``.

        In DETAIL mode the deck plate outline is inset by the surrounding girders'
        top-flange half-width so it spans the clear opening between the flanges
        instead of overlapping them; simulation mode keeps the full-cell deck
        (``deck_inset=0``), byte-identical to before."""
        deck_inset = girder_flange_width(self.girder_sec) / 2.0 if self.detail else 0.0
        deck = reinforced_floor(
            name, face.get_points(), self.pl_thick, self.stringer_sec, self.stringer_spacing, deck_inset=deck_inset
        )
        ledger.record(face, deck)
        room.add_part(deck)

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

        # Every built deck is recorded (by guid + plane) so it is plated once and can
        # afterwards be tagged onto its face — a routed riser crossing a tagged deck
        # then gets an automatic cutout + penetration detail (see the tagging pass).
        ledger = DeckLedger()

        # External floor/roof decks, grouped by the cell they belong to.
        for i, face in enumerate(floor_faces):
            self._floor(f"Floor_{i:02d}", face, ledger, room(face.parent_cell.name))

        # Internal (shared) decks between stacked cells — skip any plane already plated.
        for i, face in enumerate(internal_floors):
            if ledger.already_built(face):
                continue
            self._floor(f"IntFloor_{i:02d}", face, ledger, room(face.parent_cell.name))

        # Fully enclosed rooms: plate every bounding face of the flagged cells —
        # all four walls (external + shared internal) plus any deck face not
        # already built (e.g. the internal deck under a second-floor room).
        cell_by_name = {f.parent_cell.name: f.parent_cell for f in (*floor_faces, *internal_walls, *external_walls)}
        # Map a shared internal wall to its member object so a plated enclosed wall
        # can tag it (so penetration modelling — which walks get_internal_walls() —
        # only ever cuts through walls that were actually built).
        iw_by_key = {face_center_key(w): w for w in internal_walls}
        for cname in enclosed:
            cell = cell_by_name.get(cname)
            if cell is None:
                continue
            for j, face in enumerate(cell.faces):
                if face.is_horizontal():
                    if ledger.already_built(face):
                        continue
                    self._floor(f"Deck_{cname}_{j:02d}", face, ledger, room(cname))
                else:
                    wall = self._wall(f"Wall_{cname}_{j:02d}", face)
                    # If this bounding wall is a shared internal wall, tag the
                    # member the penetration engine sees with the built part.
                    member = iw_by_key.get(face_center_key(face))
                    if member is not None:
                        member.associated_part = wall
                    room(cname).add_part(wall)

        # Tag the deck faces the penetration engine walks (get_external_floors /
        # get_internal_floors return the canonical face objects) with the plate that
        # was actually built for them — keyed by guid so it works no matter which
        # pass built the deck. A run crossing a tagged deck now cuts a real hole.
        for face in cg.get_external_floors() + cg.get_internal_floors():
            built = ledger.by_guid.get(face.guid)
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
        # the deck plate top (= the deck line, since decks now sit their top at the
        # outline elevation), not straddling the deck line.
        girders = []
        for i, edge in enumerate(dedupe_edges(floor_faces + internal_floors, horizontal=True)):
            g = ada.Beam(f"Girder_{i:02d}", *edge.get_points()[:2], self.girder_sec)
            seat_deck_beam(g, float(g.n1.p[2]))  # flange top flush with the deck line
            girders.append(g)
        columns = [
            ada.Beam(f"Column_{i:02d}", *edge.get_points()[:2], self.column_sec)
            for i, edge in enumerate(dedupe_edges(external_walls + internal_walls, horizontal=False))
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
            pl_lo, pl_hi = plate_world_aabb(pl)
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

            exc = cut_box(pl, f"{opening.NAME}_cut", cut_lo, cut_hi)
            if exc is not None:
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
        pl_lo, pl_hi = plate_world_aabb(h_plate)
        bcut_lo, bcut_hi = h_cut_lo.copy(), h_cut_hi.copy()
        bcut_lo[h_normal_axis] = pl_lo[h_normal_axis] - _OPENING_STIFFENER_CLEAR
        bcut_hi[h_normal_axis] = pl_hi[h_normal_axis] + _OPENING_STIFFENER_CLEAR
        cut_crossing_secondary_beams(host, opening.NAME, bcut_lo, bcut_hi)

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
        width_axis, height_axis = frame_axes(normal_axis)
        pl_lo, pl_hi = plate_world_aabb(plate)
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
