"""JacketStru: an open space-frame (jacket) blueprint.

A second reference blueprint alongside :class:`~ada.topo_model.blueprint.SteelStru`.
Where ``SteelStru`` decks-out and stiffens every cell (a floored building frame),
a jacket is an *open truss*: tubular legs, a tubular ring at each station, and
diagonal braces across each bay face — no floor plates, no stringers.

It derives everything from the same classified :class:`~ada.topology.graph.CellGraph`
faces/edges ``SteelStru`` uses (so it runs through ``TopologyBuilder`` /
``ProceduralBuilder`` identically):

- wall-face vertical edges  -> legs   (deduped where two bay panels share a chord)
- floor/ring horizontal edges -> ring framing (deduped per station plane)
- each wall (bay) panel's two diagonals -> X-braces

All members are tubular; sizes are constructor knobs (metres).
"""

from __future__ import annotations

import ada
from ada.topology import BlueprintBase

from ._geometry import dedupe_edges

__all__ = ["JacketStru"]


class JacketStru(BlueprintBase):
    """Open jacket space-frame: tubular legs + ring framing + diagonal braces
    derived purely from the cell graph's classified faces/edges."""

    def __init__(
        self,
        name: str = "JacketStru",
        leg_radius: float = 0.75,
        leg_wt: float = 0.03,
        ring_radius: float = 0.5,
        ring_wt: float = 0.025,
        brace_radius: float = 0.4,
        brace_wt: float = 0.02,
        x_brace: bool = True,
    ):
        super().__init__()
        self.name = name
        self.leg_radius = leg_radius
        self.leg_wt = leg_wt
        self.ring_radius = ring_radius
        self.ring_wt = ring_wt
        self.brace_radius = brace_radius
        self.brace_wt = brace_wt
        # X-brace (two crossing diagonals per bay face); False = single diagonal.
        self.x_brace = x_brace

    def _group_prefix(self) -> str:
        return self.name

    def build(self) -> ada.Part:
        self.output_part = ada.Part(self.name)
        cg = self.builder.cell_graph

        floor_faces = cg.get_external_floors()
        internal_floors = cg.get_internal_floors()
        external_walls = cg.get_external_walls()
        internal_walls = cg.get_internal_walls()

        leg_sec = ada.Section("JacketLeg", sec_type="TUB", r=self.leg_radius, wt=self.leg_wt)
        ring_sec = ada.Section("JacketRing", sec_type="TUB", r=self.ring_radius, wt=self.ring_wt)
        brace_sec = ada.Section("JacketBrace", sec_type="TUB", r=self.brace_radius, wt=self.brace_wt)

        # Legs: the vertical chord edges of the bay panels (deduped where adjacent
        # panels share a chord) — the same non-horizontal wall edges SteelStru turns
        # into columns.
        legs = [
            ada.Beam(f"Leg_{i:02d}", *edge.get_points()[:2], leg_sec)
            for i, edge in enumerate(dedupe_edges(external_walls + internal_walls, horizontal=False))
        ]

        # Ring framing: the horizontal station-plane edges (one ring per station).
        ring = [
            ada.Beam(f"Ring_{i:02d}", *edge.get_points()[:2], ring_sec)
            for i, edge in enumerate(dedupe_edges(floor_faces + internal_floors, horizontal=True))
        ]

        # Diagonal braces: each bay (external wall) panel's two corner-to-corner
        # diagonals. A quad panel's points come round the perimeter, so (0,2) and
        # (1,3) are the crossing diagonals.
        braces = []
        for i, face in enumerate(external_walls):
            pts = face.get_points()
            if len(pts) < 4:
                continue
            braces.append(ada.Beam(f"Brace_{i:02d}a", pts[0], pts[2], brace_sec))
            if self.x_brace:
                braces.append(ada.Beam(f"Brace_{i:02d}b", pts[1], pts[3], brace_sec))

        frame = ada.Part("Jacket")
        frame.add_part(ada.Part("Legs") / legs)
        frame.add_part(ada.Part("Ring") / ring)
        if braces:
            frame.add_part(ada.Part("Braces") / braces)
        self.output_part.add_part(frame)
        return self.output_part
