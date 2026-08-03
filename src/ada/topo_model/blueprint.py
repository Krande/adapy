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

import numpy as np

import ada
from ada.topology import BlueprintBase
from ada.topology.graph import GraphEdge, GraphFace

__all__ = ["SteelStru"]

_MID_NDIGITS = 4


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


def _build_reinforced_floor(
    name: str, points: list[ada.Point], pl_thick: float, stringer_sec: str, spacing: float
) -> ada.Part:
    """A reinforced floor built from a horizontal face outline: one plate plus
    stringer beams running along the longer plan direction, evenly distributed
    across the shorter one (edge positions carry girders, so they are skipped)."""
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

    return ada.Part(name) / [plate, *stringers]


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
    ):
        super().__init__()
        self.name = name
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

        # External floor/roof decks, grouped by the cell they belong to.
        built_floor_guids: set[str] = set()
        for i, face in enumerate(floor_faces):
            built_floor_guids.add(face.guid)
            floor = _build_reinforced_floor(
                f"Floor_{i:02d}", face.get_points(), self.pl_thick, self.stringer_sec, self.stringer_spacing
            )
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
                    room(cname).add_part(deck)
                else:
                    wall = self._wall(f"Wall_{cname}_{j:02d}", face)
                    # If this bounding wall is a shared internal wall, tag the
                    # member the penetration engine sees with the built part.
                    member = iw_by_key.get(_face_center_key(face))
                    if member is not None:
                        member.associated_part = wall
                    room(cname).add_part(wall)

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
        girders = [
            ada.Beam(f"Girder_{i:02d}", *edge.get_points()[:2], self.girder_sec)
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
