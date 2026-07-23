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
    ):
        super().__init__()
        self.name = name
        self.girder_sec = girder_sec
        self.column_sec = column_sec
        self.stringer_sec = stringer_sec
        self.pl_thick = pl_thick
        self.stringer_spacing = stringer_spacing

    def _group_prefix(self) -> str:
        return self.name

    def build(self) -> ada.Part:
        self.output_part = ada.Part(self.name)
        cg = self.builder.cell_graph

        floor_faces = cg.get_external_floors()
        for i, face in enumerate(floor_faces):
            floor = _build_reinforced_floor(
                f"Floor_{i:02d}", face.get_points(), self.pl_thick, self.stringer_sec, self.stringer_spacing
            )
            self.add_to_area("floors", floor)

        girders = [
            ada.Beam(f"Girder_{i:02d}", *edge.get_points()[:2], self.girder_sec)
            for i, edge in enumerate(_dedupe_edges(floor_faces, horizontal=True))
        ]
        self.add_to_area("girders", ada.Part("Girders") / girders)

        wall_faces = cg.get_external_walls() + cg.get_internal_walls()
        columns = [
            ada.Beam(f"Column_{i:02d}", *edge.get_points()[:2], self.column_sec)
            for i, edge in enumerate(_dedupe_edges(wall_faces, horizontal=False))
        ]
        self.add_to_area("columns", ada.Part("Columns") / columns)

        self.load_parts_from_area_map()
        return self.output_part
