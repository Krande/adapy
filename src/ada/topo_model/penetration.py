"""Penetration details where routed systems cross walls/floors.

``PenetrationBlueprintBase`` is the scaffold: it intersects each system's
routed polyline with a set of planar faces (walls/floors from the cell graph)
and emits one detail part per crossing via the ``build_penetration`` override.
``StandardPenetrations`` is the reference detail standard, keyed on the
routing type: process runs get a pipe sleeve, cable/electrical runs an
MCT-style transit block, duct runs a rectangular frame — and when the crossed
face carries a built wall part (``face.associated_part``), the through-hole is
cut in its plate so the system genuinely passes through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.topology import BlueprintBase

if TYPE_CHECKING:
    from ada.api.systems.base import System
    from ada.topology.graph import GraphFace

__all__ = ["Penetration", "PenetrationBlueprintBase", "StandardPenetrations", "find_face_crossings"]


@dataclass
class Penetration:
    system: System
    point: ada.Point
    normal: ada.Direction
    face: GraphFace


def find_face_crossings(system: System, faces: list[GraphFace], tol: float = 1e-6) -> list[Penetration]:
    """Where does the system's routed polyline cross each (planar, axis-aligned
    bounded) face? Segment-plane intersection, kept when the hit lies within
    the face outline's bounding box."""
    out: list[Penetration] = []
    if not system.routed_path:
        return out
    for face in faces:
        pts = np.asarray([tuple(p) for p in face.get_points()], dtype=float)
        n = np.asarray(tuple(face.normal), dtype=float)
        p0 = pts[0]
        lo, hi = pts.min(axis=0) - tol, pts.max(axis=0) + tol
        for a, b in zip(system.routed_path[:-1], system.routed_path[1:]):
            a_ = np.asarray(tuple(a), dtype=float)
            b_ = np.asarray(tuple(b), dtype=float)
            denom = float(n.dot(b_ - a_))
            if abs(denom) < tol:
                continue  # segment parallel to the face plane
            t = float(n.dot(p0 - a_)) / denom
            if not (0.0 <= t <= 1.0):
                continue
            x = a_ + t * (b_ - a_)
            if np.all(x >= lo) and np.all(x <= hi):
                out.append(Penetration(system, ada.Point(*x), ada.Direction(*tuple(face.normal)), face))
    return out


class PenetrationBlueprintBase(BlueprintBase):
    """Blueprint scaffold: crossings of ``systems`` x ``faces`` become detail
    parts, grouped per system. Subclasses implement ``build_penetration``."""

    def __init__(self, systems: list[System], faces: list[GraphFace]):
        super().__init__()
        self.systems = list(systems)
        self.faces = list(faces)
        self.penetrations: list[Penetration] = []

    def _group_prefix(self) -> str:
        return "Penetrations"

    def find_penetrations(self) -> list[Penetration]:
        out: list[Penetration] = []
        for system in self.systems:
            out.extend(find_face_crossings(system, self.faces))
        return out

    def build_penetration(self, pen: Penetration, name: str) -> ada.Part:
        raise NotImplementedError("subclasses implement the penetration detail")

    def build(self) -> ada.Part:
        self.output_part = ada.Part("Penetrations")
        self.penetrations = self.find_penetrations()
        counts: dict[str, int] = {}
        for pen in self.penetrations:
            i = counts.get(pen.system.name, 0)
            counts[pen.system.name] = i + 1
            self.add_to_area(pen.system.name, self.build_penetration(pen, f"{pen.system.name}_pen_{i:02d}"))
        self.load_parts_from_area_map()
        return self.output_part


class StandardPenetrations(PenetrationBlueprintBase):
    """Reference detail standard by routing type (see module docstring)."""

    def __init__(
        self,
        systems: list[System],
        faces: list[GraphFace],
        sleeve_clearance: float = 0.02,
        sleeve_wt: float = 8e-3,
        depth: float = 0.3,
        cable_block_size: float = 0.3,
        duct_frame_size: float = 0.45,
    ):
        super().__init__(systems, faces)
        self.sleeve_clearance = sleeve_clearance
        self.sleeve_wt = sleeve_wt
        self.depth = depth
        self.cable_block_size = cable_block_size
        self.duct_frame_size = duct_frame_size

    def build_penetration(self, pen: Penetration, name: str) -> ada.Part:
        from ada.api.systems.base import DuctSystem, PipingSystem

        n = np.asarray(tuple(pen.normal), dtype=float)
        n /= np.linalg.norm(n)
        x = np.asarray(tuple(pen.point), dtype=float)
        p1 = tuple(x - n * self.depth / 2)
        p2 = tuple(x + n * self.depth / 2)

        if isinstance(pen.system, PipingSystem):
            hole_r = pen.system.pipe_radius + self.sleeve_clearance
            detail: ada.Shape = ada.PrimCyl(f"{name}_sleeve", p1, p2, hole_r + self.sleeve_wt, color="red")
            hole = ada.PrimCyl(f"{name}_hole", p1, p2, hole_r)
        else:
            half = (self.duct_frame_size if isinstance(pen.system, DuctSystem) else self.cable_block_size) / 2
            in_plane = np.array([half, half, half]) * (1.0 - np.abs(n))
            lo = x - in_plane - np.abs(n) * self.depth / 2
            hi = x + in_plane + np.abs(n) * self.depth / 2
            detail = ada.PrimBox(f"{name}_block", tuple(lo), tuple(hi), color="red")
            shrink = 0.8  # the transit frame keeps a rim; the hole is the inner opening
            lo_h = x - in_plane * shrink - np.abs(n) * self.depth
            hi_h = x + in_plane * shrink + np.abs(n) * self.depth
            hole = ada.PrimBox(f"{name}_hole", tuple(lo_h), tuple(hi_h))

        self._cut_wall_hole(pen, hole)
        return ada.Part(name) / detail

    def _cut_wall_hole(self, pen: Penetration, hole: ada.Shape) -> None:
        """Cut the through-opening in the crossed face's built wall plate(s),
        when the face carries one (``face.associated_part``)."""
        wall_part = pen.face.associated_part
        if wall_part is None:
            return
        for pl in wall_part.get_all_physical_objects(by_type=ada.Plate):
            pl.add_boolean(hole)
